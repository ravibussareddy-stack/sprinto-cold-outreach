"""Step 1 - Scrape. A single-fetch scraper (lowest scope, cost and legal
exposure). Request -> parse -> extract -> clean -> store (cache).

Order of attempts (L4 "order you try things in"):
  1. robots.txt check - if disallowed, stop for that URL.
  2. Plain request (cheap).
  3. If the page is thin (JS-rendered shell), fall back to Jina Reader,
     which runs a real browser and returns clean text.
  4. On any failure: record the reason and move on (degrade gracefully).
"""
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import config
import db

session = requests.Session()
session.headers.update({"User-Agent": config.USER_AGENT,
                        "Accept-Language": "en-US,en;q=0.9"})

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

# Certification strings are often only visible as footer badges / image alt
# text, which the cleaning step throws away. So we look for them in the RAW
# html before cleaning.
CERT_PATTERNS = {
    "SOC 2": r"\bSOC[\s\-]?2\b|\bSOC\s?II\b",
    "ISO 27001": r"ISO[\s/\-]?(?:IEC[\s\-]?)?27001",
    "HIPAA": r"\bHIPAA\b",
    "GDPR": r"\bGDPR\b",
    "PCI DSS": r"\bPCI[\s\-]?DSS\b",
}
IN_PROGRESS_PATTERN = r"(SOC[\s\-]?2|ISO[\s\-]?27001)[^.]{0,60}(in progress|underway|pursuing|working towards|coming soon|pending)"

NOISE_TAGS = ["script", "style", "noscript", "svg", "iframe", "form", "nav",
              "footer", "header", "aside", "button"]
NOISE_HINTS = re.compile(r"cookie|consent|banner|navbar|menu|footer|modal|popup|newsletter", re.I)


def robots_allows(url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = session.get(base + "/robots.txt", timeout=config.REQUEST_TIMEOUT)
            if r.status_code >= 400:
                rp = None  # no robots.txt -> no stated restrictions
            else:
                rp.parse(r.text.splitlines())
        except requests.RequestException:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(config.USER_AGENT, url)


# Who a certification mention is about. A page can mention SOC 2 because the company holds it,
# because it helps customers get it, or because a vendor (AWS, Stripe) holds it. Only the first
# counts as a claim; the others are kept as evidence but never trigger the dormant rule.
ABOUT_CUSTOMERS = re.compile(
    r"help(s|ing)?\s+(you|your|customers|teams|companies|businesses|startups|organi[sz]ations)|"
    r"\b(get|achieve|become|prepare for|automate|accelerate|simplify|streamline)\b[^.]{0,40}$|"
    r"\byour\s+(soc|iso|audit|compliance)|for\s+your\b|customers?\s+(get|achieve|become)|"
    r"customers who (have|need)|obligations themselves", re.I)
ABOUT_VENDOR = re.compile(
    r"\b(aws|amazon web services|azure|google cloud|gcp|stripe|vercel|heroku|cloudflare|sub-?processors?|"
    r"our\s+(vendors?|providers?|hosting|infrastructure|data cent(er|re)s?)|hosted (on|with|by))\b", re.I)
ABOUT_SELF = re.compile(
    r"\b(we|we're|we are|we have|we've|our (company|platform|product|service|app|organi[sz]ation)|is|are|has|have|been)\b"
    r"[^.]{0,60}\b(certified|compliant|audited|attested|attestation|achieved|completed|accredited)\b|"
    r"type\s?(i|ii|1|2)\b[^.]{0,30}\b(report|certified|compliant|attestation|audit)|"
    r"\b(certified|compliant|audited)\b|trust (center|centre|portal)|request (our|the|a) (soc|report|copy)", re.I)

# Compliance automation platforms. A company already running one is a switch conversation, not greenfield.
COMPLIANCE_TOOLS = ["Vanta", "Drata", "Secureframe", "Thoropass", "Scrut", "Sprinto", "Laika", "Tugboat Logic",
                    "Hyperproof", "Strike Graph"]
TOOL_CONTEXT = re.compile(r"powered by|trust (center|centre|portal)|we use|uses|using|with|monitored|"
                          r"compliance|automat|continuous|invited to|policies|security training|onboarding", re.I)


def _window(text: str, start: int, end: int, before: int = 140, after: int = 100) -> tuple[str, str, str]:
    pre, post = text[max(0, start - before):start], text[end:end + after]
    pre = re.split(r"[.!?\n]\s", pre)[-1]
    post = re.split(r"[.!?\n]", post)[0]
    return pre, text[start:end], post


def classify_mention(pre: str, post: str, hit: str = "SOC 2") -> str:
    """self | customers | vendor | unclear, from the words around one mention."""
    around = f"{pre}{hit}{post}"
    if ABOUT_CUSTOMERS.search(pre) or ABOUT_CUSTOMERS.search(around):
        return "customers"
    if ABOUT_VENDOR.search(around):
        return "vendor"
    if ABOUT_SELF.search(around):
        return "self"
    return "unclear"


def sitemap_urls(base: str, pattern: str, limit: int = 5) -> list[str]:
    """URLs from the site's sitemap whose path matches `pattern` (shortest first). Empty on any error."""
    out = []
    for path in ("/sitemap.xml", "/sitemap_index.xml"):
        url = base + path
        if not robots_allows(url):
            continue
        try:
            r = session.get(url, timeout=8)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            continue
        locs = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", r.text)
        subs = [u for u in locs if u.endswith(".xml")][:3]         # a sitemap index: look one level down
        for s in subs:
            try:
                rr = session.get(s, timeout=8)
                if rr.status_code == 200:
                    locs += re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", rr.text)
            except requests.RequestException:
                pass
        out = [u for u in locs if not u.endswith(".xml") and re.search(pattern, urlparse(u).path, re.I)
               and "/blog" not in u and "/news" not in u]
        if out:
            break
    return sorted(set(out), key=len)[:limit]


def detect_certifications(raw_text: str) -> dict:
    """mentioned: every certification named anywhere (kept for the model as a hint).
    self_claimed: those with at least one mention that reads as the company's own claim, or a
    bare badge-style mention ("unclear") - the caller decides how far to trust "unclear".
    evidence: up to 6 sentences with who each one is about. tools: compliance platforms in use."""
    text = re.sub(r"\s+", " ", raw_text or "")
    found, evidence, self_claimed, unclear = [], [], set(), set()
    for name, pat in CERT_PATTERNS.items():
        for m in list(re.finditer(pat, text, re.I))[:5]:
            pre, hit, post = _window(text, m.start(), m.end())
            if re.search(r"\b(neither|nor|not|no|don'?t|doesn'?t|do not|does not|without|yet to|haven'?t|isn'?t|aren'?t)\b",
                         pre[-35:], re.I) or re.search(r"^\W{0,3}(is|are)?\s*(not|n't)\b", post[:15], re.I):
                who = "denied"                          # "we don't have SOC 2 yet", "neither SOC 2 nor ISO 27001"
            elif re.search(r"in progress|underway|pursuing|working towards|coming soon|pending|preparing for|on track",
                           pre[-40:] + post[:60], re.I):
                who = "in_progress"
            else:
                who = classify_mention(pre, post, hit)
            if name not in found:
                found.append(name)
            if who == "self":
                self_claimed.add(name)
            elif who == "unclear":
                unclear.add(name)
            sentence = f"{pre}{hit}{post}".strip()[:240]
            clear = bool(re.search(r"\b(is|are|we're|we are)\b[^.]{0,30}\b(certified|compliant|audited|attested)\b", sentence, re.I))
            same = next((e for e in evidence if e["cert"] == name and e["who"] == who), None)
            if same is None and len(evidence) < 6:
                evidence.append({"cert": name, "who": who, "sentence": sentence, "_clear": clear})
            elif same is not None and clear and not same["_clear"]:      # keep the plainest statement of the claim
                same.update(sentence=sentence, _clear=True)
    tools = []
    for tool in COMPLIANCE_TOOLS:
        for m in re.finditer(rf"\b{re.escape(tool)}\b", text):
            pre, hit, post = _window(text, m.start(), m.end(), 80, 80)
            ctx = pre + " " + post
            if TOOL_CONTEXT.search(ctx) and not re.search(r"integrat|partner|marketplace|connect(s|or)", ctx, re.I):
                tools.append({"tool": tool, "sentence": f"{pre}{hit}{post}".strip()[:240]})
                break
    in_progress = bool(re.search(IN_PROGRESS_PATTERN, text, re.I))
    for e in evidence:
        e.pop("_clear", None)
    return {"mentioned": found, "self_claimed": sorted(self_claimed), "unclear": sorted(unclear - self_claimed),
            "in_progress": in_progress, "evidence": evidence, "tools": tools}


NOT_FOUND = re.compile(r"\b(error\s*404|404\s*(error|page|not found)|page (not found|does ?n[o'\u2019]?t exist|could not be found)|"
                       r"this page (does ?n[o'\u2019]?t|doesn['\u2019]t) exist|we can['\u2019]?t find (that|the|this) page|nothing (was )?found here)", re.I)


def is_not_found_page(text: str) -> bool:
    """A 'soft 404': the site answers 200 (or the reader renders) its own not-found page. Short page plus
    not-found wording = not found; a long page that merely links to a 404 help article is not."""
    words = len((text or "").split())
    return bool(NOT_FOUND.search(text or "")) and words < 400


def clean_html(html: str) -> str:
    """Rules from L4: drop nav/footer/cookie banners, drop lines under eight
    words (except headings), drop duplicates, cap length."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for el in soup.find_all(attrs={"class": NOISE_HINTS}) + soup.find_all(attrs={"id": NOISE_HINTS}):
        if el.name not in ("body", "html", "main"):
            el.decompose()
    lines, seen = [], set()
    for el in soup.find_all(["h1", "h2", "h3", "p", "li", "td"]):
        text = " ".join(el.get_text(" ", strip=True).split())
        if not text or text.lower() in seen:
            continue
        is_heading = el.name in ("h1", "h2", "h3")
        if not is_heading and len(text.split()) < 8:
            continue
        seen.add(text.lower())
        lines.append(("## " if is_heading else "") + text)
    words = "\n".join(lines).split(" ")
    return " ".join(words[: config.MAX_CLEAN_WORDS])


def extract_links(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"]).split("#")[0]
        if href in seen or not href.startswith("http"):
            continue
        seen.add(href)
        out.append({"url": href, "text": " ".join(a.get_text(" ", strip=True).split())[:60]})
    return out


def _jina(url: str) -> str | None:
    try:
        r = session.get("https://r.jina.ai/" + url, timeout=40,
                        headers={"X-Return-Format": "text"})
        if r.status_code == 200 and len(r.text.split()) > 50:
            return r.text
    except requests.RequestException:
        pass
    return None


def fetch(url: str) -> dict:
    """Returns {url, ok, error, text, raw_text, certs, links, method}."""
    cached = db.get_cached_page(url)
    if cached and not is_not_found_page(cached.get("text", "")):     # pages cached before soft-404 checks are re-fetched
        return cached

    result = {"url": url, "ok": False, "error": None, "text": "", "raw_text": "",
              "certs": {"mentioned": [], "in_progress": False}, "links": [], "method": None}

    if not robots_allows(url):
        result["error"] = "disallowed_by_robots_txt"
        return result

    html = None
    try:
        r = session.get(url, timeout=config.REQUEST_TIMEOUT, allow_redirects=True)
        time.sleep(config.DELAY_BETWEEN_REQUESTS)
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            html = r.text
            result["url"] = r.url
        else:
            result["error"] = f"http_{r.status_code}"
    except requests.RequestException as e:
        result["error"] = f"request_failed: {type(e).__name__}"

    if html:
        soup_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        alt_text = " ".join(img.get("alt", "") for img in BeautifulSoup(html, "html.parser").find_all("img"))
        result["raw_text"] = soup_text + " " + alt_text
        result["text"] = clean_html(html)
        result["links"] = extract_links(html, result["url"])
        result["method"] = "request"

    # Thin page (JS shell) or blocked (403 from Cloudflare etc.) -> Jina Reader. Never for a page that
    # does not exist (404/410): the reader would return the site's "not found" page as if it were content.
    gone = result["error"] in ("http_404", "http_410")
    if config.USE_JINA_FALLBACK and not gone and len(result["text"].split()) < config.THIN_PAGE_WORDS:
        jt = _jina(url)
        if jt:
            result["raw_text"] = (result["raw_text"] + " " + jt).strip()
            # Jina already strips boilerplate; still dedupe + cap.
            seen, keep = set(), []
            for line in jt.splitlines():
                line = line.strip()
                if len(line.split()) >= 4 and line.lower() not in seen:
                    seen.add(line.lower()); keep.append(line)
            result["text"] = " ".join("\n".join(keep).split(" ")[: config.MAX_CLEAN_WORDS])
            result["links"] = result["links"] or [
                {"url": m, "text": ""} for m in re.findall(r"https?://[^\s)\]]+", jt)]
            result["method"] = "jina_reader"
            result["error"] = None

    result["ok"] = len(result["text"].split()) >= 30
    if result["ok"] and is_not_found_page(result["text"]):
        result["ok"], result["error"] = False, "not_found_page"      # a "soft 404": served as 200
    if not result["ok"] and not result["error"]:
        result["error"] = "no_usable_text"
    result["certs"] = detect_certifications(result["raw_text"])
    result["raw_text"] = result["raw_text"][:20000]

    if result["ok"]:
        db.put_cached_page(url, result)
    return result
