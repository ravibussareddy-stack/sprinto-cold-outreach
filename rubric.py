"""The qualification rubric: every filter and point value in one place.

Defaults live in lib/rubric_defaults.json (the dashboard reads the same file).
Edits made on the dashboard's "Filters & rubric" page are stored in Neon (settings table,
key "rubric") and override the defaults from the next run on. Unknown keys and bad values
are ignored, so a broken edit can never stop a run - the default is used instead.

    import rubric
    rubric.get("decision", "qualify_score")
"""
import copy
import json
import os

import db

DEFAULTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib", "rubric_defaults.json")
DORMANT_RULES = ("any_named_cert", "soc2_and_iso", "off")
_active: dict | None = None


def defaults() -> dict:
    with open(DEFAULTS_PATH) as f:
        return json.load(f)


def merge(base: dict, override: dict | None) -> dict:
    """Defaults overlaid with valid stored values only."""
    out = copy.deepcopy(base)
    for section, values in base.items():
        given = (override or {}).get(section)
        if not isinstance(given, dict):
            continue
        for key, default in values.items():
            v = given.get(key)
            if v is None:
                continue
            if isinstance(default, bool):
                if isinstance(v, bool):
                    out[section][key] = v
            elif isinstance(default, (int, float)):
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    out[section][key] = v
            elif isinstance(default, list):
                if isinstance(v, list):
                    out[section][key] = [str(x).strip().lower() for x in v if str(x).strip()]
            elif isinstance(default, str):
                if isinstance(v, str) and v.strip():
                    out[section][key] = v.strip()
    if out["certifications"]["dormant_rule"] not in DORMANT_RULES:
        out["certifications"]["dormant_rule"] = base["certifications"]["dormant_rule"]
    if out["decision"]["review_score"] > out["decision"]["qualify_score"]:
        out["decision"] = copy.deepcopy(base["decision"])       # inconsistent thresholds: use defaults
    if out["size"]["min_employees"] > out["size"]["max_employees"]:
        out["size"] = copy.deepcopy(base["size"])
    return out


def load(refresh: bool = False) -> dict:
    """The active rubric (cached for the run)."""
    global _active
    if _active is None or refresh:
        stored = db.get_setting("rubric") if db.enabled() else None
        _active = merge(defaults(), stored)
    return _active


def use(r: dict):
    """Pin the rubric (tests, or a one-off run)."""
    global _active
    _active = merge(defaults(), r)


def get(section: str, key: str):
    return load()[section][key]
