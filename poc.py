"""Step 4 - Find the right point of contact.

Option A: Hunter.io Domain Search API (free tier: 25 searches/month).
Option B: Apollo.io lookups done by hand, hard-coded in poc_overrides.json.
Overrides always win, so you can save Hunter credits on known companies.

Who is "right" depends on company size - the person who OWNS getting the
certification and feels the pain of security questionnaires:
  * < 50 people   -> founder/CEO or CTO (no security team exists yet)
  * 50-200 people -> CTO / VP Eng, unless a security/compliance lead exists
  * > 200 people  -> Head of Security / CISO / GRC / Compliance lead first
"""
import json
import os
import re

import requests

import config
from retry import PermanentError, with_retry

OVERRIDES_FILE = os.path.join(os.path.dirname(__file__), "poc_overrides.json")

SECURITY = re.compile(r"\b(ciso|chief information security|security|grc|compliance|trust|risk|infosec)\b", re.I)
TECH_EXEC = re.compile(r"\b(cto|chief technology|vp,? engineering|vp of engineering|head of engineering|director of engineering)\b", re.I)
FOUNDER = re.compile(r"\b(ceo|chief executive|co-?founder|founder)\b", re.I)
OPS_EXEC = re.compile(r"\b(coo|chief operating|head of it|it director)\b", re.I)
SENIOR = re.compile(r"\b(chief|head|vp|vice president|director|founder|lead|c[a-z]o)\b", re.I)
EXCLUDE = re.compile(r"\b(intern|recruit|talent|sales|marketing|support|success|designer|analyst)\b", re.I)


def _rank(title: str, size: int | None) -> int | None:
    t = title or ""
    if not t or EXCLUDE.search(t):
        return None
    is_sec = SECURITY.search(t) and SENIOR.search(t)
    if size is not None and size < 50:
        order = [FOUNDER, TECH_EXEC, OPS_EXEC]
        if is_sec:
            return 1
    elif size is not None and size > 200:
        if is_sec:
            return 0
        order = [TECH_EXEC, OPS_EXEC, FOUNDER]
    else:
        if is_sec:
            return 0
        order = [TECH_EXEC, FOUNDER, OPS_EXEC]
    for i, pat in enumerate(order):
        if pat.search(t):
            return i + 2
    return None


def _load_overrides() -> dict:
    if os.path.exists(OVERRIDES_FILE):
        with open(OVERRIDES_FILE) as f:
            return json.load(f)
    return {}


def find_poc(domain: str, employee_estimate: int | None) -> dict:
    domain = domain.replace("https://", "").replace("http://", "").replace("www.", "").strip("/")
    ov = _load_overrides().get(domain)
    if ov:
        return {**ov, "source": "manual (Apollo lookup)",
                "why": ov.get("why", "Looked up manually in Apollo and hard-coded.")}

    if not config.HUNTER_API_KEY:
        return {"error": "no HUNTER_API_KEY and no entry in poc_overrides.json"}

    def _call():
        r = requests.get("https://api.hunter.io/v2/domain-search",
                         params={"domain": domain, "api_key": config.HUNTER_API_KEY, "limit": 10},
                         timeout=20)
        if r.status_code == 200:
            return r
        msg = f"hunter http {r.status_code}: {r.text[:200]}"
        if r.status_code == 429 or r.status_code >= 500:
            raise requests.RequestException(msg)        # transient: retried
        raise PermanentError(msg)                       # bad key / plan limit: stop now

    try:
        r = with_retry(_call, transient=(requests.RequestException,))
    except PermanentError as e:
        return {"error": str(e)}
    except requests.RequestException as e:
        return {"error": f"hunter unreachable after retries: {e}"}
    people = r.json().get("data", {}).get("emails", [])

    ranked = []
    for p in people:
        if not (p.get("first_name") and p.get("value")):
            continue
        rk = _rank(p.get("position") or "", employee_estimate)
        if rk is not None:
            ranked.append((rk, -(p.get("confidence") or 0), p))
    if not ranked:
        titles = [p.get("position") for p in people if p.get("position")]
        return {"error": "no decision-maker found", "titles_seen": titles}
    ranked.sort(key=lambda x: (x[0], x[1]))
    best = ranked[0][2]
    size_band = ("<50" if employee_estimate and employee_estimate < 50
                 else ">200" if employee_estimate and employee_estimate > 200
                 else "50-200 / unknown")
    return {
        "first_name": best["first_name"], "last_name": best.get("last_name") or "",
        "email": best["value"], "title": best.get("position"),
        "linkedin": best.get("linkedin"), "confidence": best.get("confidence"),
        "source": "hunter.io",
        "why": (f"Company size band {size_band}: chose '{best.get('position')}' as the person who owns "
                f"security/compliance outcomes at this stage (ranked {len(ranked)} senior candidates "
                f"out of {len(people)} contacts; Hunter confidence {best.get('confidence')}%)."),
        "alternatives": [f"{p['first_name']} {p.get('last_name') or ''} - {p.get('position')}" for _, _, p in ranked[1:4]],
    }
