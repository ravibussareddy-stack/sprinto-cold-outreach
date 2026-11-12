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


def detect_certifications(raw_text: str) -> dict:
    found = [name for name, pat in CERT_PATTERNS.items() if re.search(pat, raw_text, re.I)]
    in_progress = bool(re.search(IN_PROGRESS_PATTERN, raw_text, re.I))
    return {"mentioned": found, "in_progress": in_progress}


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
    if cached:
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

    # Thin page (JS shell) or blocked (403 from Cloudflare etc.) -> Jina Reader.
    if config.USE_JINA_FALLBACK and len(result["text"].split()) < config.THIN_PAGE_WORDS:
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
    if not result["ok"] and not result["error"]:
        result["error"] = "no_usable_text"
    result["certs"] = detect_certifications(result["raw_text"])
    result["raw_text"] = result["raw_text"][:20000]

    if result["ok"]:
        db.put_cached_page(url, result)
    return result
