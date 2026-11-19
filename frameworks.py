"""New certifications and frameworks: which are in your portfolio, which are new, who needs them.

The catalogue lives in lib/frameworks.json (read by the dashboard too, so both sides apply the same
fit rules). Your choices live in the Neon settings table, key "portfolio":
    {"accepted": ["iso_42001"], "dismissed": ["cmmc"], "custom": [{framework you added yourself}]}
An accepted framework re-opens certified (dormant) companies that fit it as expansion prospects.
"""
import json
import os
import re

import db

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "frameworks.json")
CATALOGUE = json.load(open(_PATH))
CORE = CATALOGUE["core"]
BY_ID = {f["id"]: f for f in CATALOGUE["frameworks"]}


def _raw() -> dict:
    s = db.get_setting("portfolio") if db.enabled() else None
    return s if isinstance(s, dict) else {}


def all_by_id(raw: dict | None = None) -> dict:
    """The catalogue plus the frameworks you added yourself (Offerings page)."""
    raw = _raw() if raw is None else raw
    out = dict(BY_ID)
    for c in raw.get("custom", []) or []:
        if isinstance(c, dict) and c.get("id") and c.get("fit", {}).get("keywords"):
            out[c["id"]] = c
    return out


def state() -> dict:
    raw = _raw()
    known = all_by_id(raw)
    return {"accepted": [i for i in raw.get("accepted", []) if i in known],
            "dismissed": [i for i in raw.get("dismissed", []) if i in known],
            "custom": [c for c in raw.get("custom", []) if isinstance(c, dict)]}


def accepted() -> list[dict]:
    raw = _raw()
    known = all_by_id(raw)
    return [known[i] for i in raw.get("accepted", []) if i in known]


def portfolio_names() -> list[str]:
    return CORE + [f["name"] for f in accepted()]


def _text(profile: dict) -> str:
    parts = [profile.get("company_name"), profile.get("one_line_description"), profile.get("buyer_type")]
    parts += profile.get("target_industries") or []
    parts += profile.get("sensitive_data_types") or []
    return " ".join(p for p in parts if p).lower()


def _hit(words: list[str], text: str) -> str | None:
    for w in words:
        if re.search(rf"\b{re.escape(w)}\b", text, re.I):
            return w
    return None


def fit(fw: dict, profile: dict, held: list[str]) -> str | None:
    """Why this company needs the framework (one short reason), or None.
    Never for a framework it already holds; market-bound ones need a stated market there."""
    if fw["family"].lower() in {h.lower() for h in held}:
        return None
    rule = fw["fit"]
    kw = _hit(rule["keywords"], _text(profile))
    if not kw:
        return None
    if rule.get("markets"):
        markets = " ".join(profile.get("geographic_markets") or []).lower()
        mk = _hit(rule["markets"], markets)
        if not mk:
            return None
        return f'"{kw}" in what they do, and they sell in {mk.upper() if len(mk) <= 4 else mk.title().replace("Eu ", "EU ")}'
    return f'"{kw}" in what they do'


def expansions(profile: dict, held: list[str], frameworks: list[dict] | None = None) -> list[dict]:
    """Accepted frameworks this company fits: [{"id", "name", "reason", "pitch"}]."""
    out = []
    for fw in (accepted() if frameworks is None else frameworks):
        why = fit(fw, profile, held)
        if why:
            out.append({"id": fw["id"], "name": fw["name"], "reason": why, "pitch": fw["pitch"]})
    return out


def portfolio_note() -> str:
    """One line for the writer and the fact check: what Sprinto covers right now."""
    return ("\nFrameworks Sprinto covers (the user's current portfolio): " + ", ".join(portfolio_names()) + ".")
