"""Keep company data fresh: crawl reputable news feeds and re-research stale companies, every day.

Runs from the daily Vercel cron (/api/cron_check_replies, after the reply check) and from the
dashboard's "Crawl now". Two parts:

1. News feeds (lib/sources.json: TechCrunch, Inc42, YourStory, EU-Startups, Sifted - public RSS,
   robots.txt checked on every fetch). New articles go into the news_items table; funding headlines
   ("Acme raises $12M Series A") are parsed into company, round and amount, and matched to the
   companies you track. A matched article is read as evidence the next time that company is researched,
   so its funding round arrives with the article as the source.
2. Re-research: the stalest companies (last researched REFRESH_DAYS+ ago; prospects before dormant ones)
   are qualified again - no email is written or sent - and what changed (decision, score, certificates,
   funding, headcount) is logged.

Nothing here blocks: a feed that fails is skipped, a company that fails is logged, and it stops when the
time budget or the monthly model budget runs out.

    python crawl.py             # feeds + up to 3 companies
"""
import datetime as dt
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import db
import scraper

SOURCES = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "sources.json")))["feeds"]
REFRESH_DAYS = 7
MAX_COMPANIES = 3

FUNDING = re.compile(
    r"^(?P<co>[A-Z0-9][\w.&'’ -]{1,48}?)\s+(?:raises|raised|secures|bags|lands|closes|nets|gets|picks up|scores)\s+"
    r"(?P<amt>(?:[$€£₹]|Rs\.?\s?|INR\s?|USD\s?)[\d.,]+\s?(?:[MBK]|Mn|mn|million|billion|Cr|crore|lakh)?)?"
    r"(?P<rest>.*)$", re.I)
ROUND = re.compile(r"\b(pre-seed|seed|series [a-f]\b|pre-series [a-f]|growth|bridge)", re.I)


# ---------------------------------------------------------------- feeds
def parse_feed(xml_text: str) -> list[dict]:
    """RSS items -> [{title, url, summary, published}] (summary without HTML)."""
    out = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return out
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        url = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
        desc = re.sub(r"\s+", " ", desc).strip()[:600]
        try:
            pub = parsedate_to_datetime(it.findtext("pubDate")) if it.findtext("pubDate") else None
        except (TypeError, ValueError):
            pub = None
        if title and url:
            out.append({"title": html.unescape(title), "url": url, "summary": desc, "published": pub})
    return out


def funding_of(title: str) -> dict | None:
    """'Acme raises $12M Series A to ...' -> {company, amount, round}; None if it is not a funding headline."""
    m = FUNDING.match(title.strip())
    if not m:
        return None
    rnd = ROUND.search(m.group("rest") or "")
    # "Physical AI startup Humyn Labs", "YC-backed LATO", "Ghent-based IntelliProve" -> the name itself
    co = re.sub(r"^.*?(?:\bstartups?|\bplatform|\bcompany|\bfirm|\bunicorn|\bmaker|-based|-backed|-led)\s+", "", m.group("co"), flags=re.I)
    return {"company": co.strip(" -"), "amount": (m.group("amt") or "").strip() or None,
            "round": rnd.group(1).title() if rnd else None}


def crawl_feeds(log=print) -> dict:
    stats = {}
    for src in SOURCES:
        if not scraper.robots_allows(src["url"]):
            stats[src["name"]] = "blocked by robots.txt"
            continue
        try:
            r = scraper.session.get(src["url"], timeout=15)
            if r.status_code != 200:
                stats[src["name"]] = f"http {r.status_code}"
                continue
            items = parse_feed(r.text)
        except Exception as e:                        # one feed failing never stops the others
            stats[src["name"]] = f"failed: {type(e).__name__}"
            continue
        rows = []
        for it in items:
            f = funding_of(it["title"]) or {}
            rows.append((it["url"], src["name"], it["title"], it["summary"], it["published"],
                         f.get("company"), f.get("round"), f.get("amount")))
        new = db.add_news_items(rows) or 0
        stats[src["name"]] = f"{len(items)} items, {new} new"
        log(f"  feed {src['name']}: {stats[src['name']]}")
    return stats


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def match_companies(log=print) -> list[dict]:
    """Funding headlines about a company you track: the headline's company equals its name, or the
    article mentions its domain. Common-word names (Plane, Twenty) only match a funding headline that
    starts with them, never a stray word in a sentence."""
    tracked = db.tracked_companies() or []
    found = []
    pairs = []
    for item in db.unmatched_news() or []:
        guess, text = _norm(item.get("company_guess")), f"{item['title']} {item.get('summary') or ''}".lower()
        for t in tracked:
            name, domain = _norm(t.get("company")), (t.get("domain") or "").lower()
            if (guess and name and guess == name) or (domain and domain in text):
                pairs.append((t["domain"], item["url"]))
                found.append({"domain": t["domain"], "title": item["title"], "source": item["source"]})
                log(f"  news: {t['domain']} <- {item['title']}")
                break
    db.match_news_many(pairs)
    return found


def news_pages(domain: str, months: int = 18) -> list[dict]:
    """Articles matched to this company, as pages the research step reads (source = the article)."""
    out = []
    for n in db.news_for(domain, months) or []:
        text = f"{n['title']}. {n.get('summary') or ''} (Reported by {n['source']}"
        text += f", {n['published']:%d %b %Y})" if n.get("published") else ")"
        out.append({"url": n["url"], "ok": True, "error": None, "method": f"news: {n['source']}", "text": text,
                    "raw_text": text, "certs": scraper.detect_certifications(""), "links": []})
    return out


# ---------------------------------------------------------------- re-research
FIELDS = (("decision", lambda q, p: q.get("decision")), ("score", lambda q, p: q.get("score")),
          ("certificates", lambda q, p: sorted(q.get("certs_held") or [])),
          ("funding", lambda q, p: (p or {}).get("funding_stage")), ("employees", lambda q, p: (p or {}).get("employee_estimate")))


def changes(old: dict, new: dict) -> list[dict]:
    oq, nq = old.get("qualification") or {}, new.get("qualification") or {}
    op, np_ = old.get("profile") or {}, new.get("profile") or {}
    out = []
    for name, get in FIELDS:
        a, b = get(oq, op), get(nq, np_)
        if a != b and b not in (None, [], ""):
            out.append({"field": name, "before": a, "after": b})
    return out


def refresh(limit: int = MAX_COMPANIES, deadline: float | None = None, log=print) -> list[dict]:
    import budget
    import llm
    from qualify import qualify
    done = []
    for t in db.stale_companies(REFRESH_DAYS, limit) or []:
        if deadline and time.time() > deadline:
            log("  time budget used: the rest wait for tomorrow")
            break
        if budget.status()["paused"]:
            log("  model budget reached: re-research paused")
            break
        d = t["domain"]
        llm.context["domain"] = d
        t0 = time.time()
        try:
            q = qualify(d, log=lambda m: None)
        except Exception as e:
            done.append({"domain": d, "error": f"{type(e).__name__}: {e}"})
            continue
        rec = {"domain": d, "source": "refresh", "status": q["decision"],
               "qualification": {k: v for k, v in q.items() if k not in ("profile", "pages")},
               "profile": q["profile"].model_dump() if q.get("profile") else None,
               "pages_read": [p["url"] for p in q.get("pages", [])], "seconds": round(time.time() - t0, 1)}
        rec["changes"] = changes(t["record"], rec)
        rec["run_id"] = db.save_run(rec)
        done.append({"domain": d, "decision": q["decision"], "changes": rec["changes"], "run_id": rec["run_id"]})
        log(f"  re-researched {d}: {q['decision']}" + (f" - changed: {', '.join(c['field'] for c in rec['changes'])}" if rec["changes"] else " - no change"))
    return done


FEEDS_EVERY_MIN = 60          # news feeds: at most hourly (cheap; continuous through the day)
REFRESH_EVERY_HOURS = 24      # re-research: once a day (it uses the model budget)


def due() -> dict:
    """What is due now, given the last crawl: {"feeds": bool, "refresh": bool}."""
    last = db.last_crawl_times() or {}
    now = dt.datetime.now(dt.timezone.utc)
    feeds_at, refresh_at = last.get("feeds"), last.get("refresh")
    return {"feeds": not feeds_at or now - feeds_at >= dt.timedelta(minutes=FEEDS_EVERY_MIN),
            "refresh": not refresh_at or now - refresh_at >= dt.timedelta(hours=REFRESH_EVERY_HOURS)}


def run(limit: int = MAX_COMPANIES, time_budget: float = 200, trigger: str = "cron", log=print,
        force: bool = False) -> dict:
    """Feeds (if an hour has passed), matching, then re-research (if a day has passed), within the
    time budget. force=True does both now ("Crawl now"). Logged in crawl_log."""
    start = time.time()
    when = {"feeds": True, "refresh": True} if force else due()
    if not when["feeds"] and not when["refresh"]:
        return {"trigger": trigger, "skipped": "nothing due: feeds are crawled hourly, re-research daily"}
    feeds = crawl_feeds(log) if when["feeds"] else "not due"
    news = match_companies(log) if when["feeds"] else []
    refreshed = refresh(limit, deadline=start + time_budget, log=log) if when["refresh"] else "not due"
    summary = {"trigger": trigger, "feeds": feeds, "news_matched": news, "refreshed": refreshed,
               "seconds": round(time.time() - start, 1), "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    db.log_crawl(summary)
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), indent=1, default=str))
