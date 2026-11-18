"""Feedback repo: every piece of guidance the writer learns from, in one Neon table.

Sources:
  human   - added by you (python feedback.py add "...")
  outcome - written by learn.py after each finished conversation, scored by its outcome

Each item has a scope (global, role:<role>, industry:<industry>) and an outcome score 0..1
(booked 1.0, positive reply 0.8, negative 0.3, no reply 0.1; human items default to 0.8).
When the writer drafts an email it gets the items relevant to this recipient, ranked by
outcome score (newest first among equals), with the rule: if two conflict, follow the
higher-ranked one. So guidance backed by better outcomes wins.

    python feedback.py list
    python feedback.py add "Open with a named customer, not an award" --scope role:cto --score 0.8
"""
import argparse
import re

import db

# Title keywords -> role scope. First match wins, so the more specific ones come first.
ROLE_KEYWORDS = [
    (r"\bciso\b|security|compliance|grc|trust", "security"),
    (r"\bcto\b|technology|engineering|engineer", "cto"),
    (r"\bceo\b|chief executive", "ceo"),
    (r"founder", "founder"),
    (r"\bcoo\b|operations", "operations"),
]


def role_of(title: str | None) -> str | None:
    t = (title or "").lower()
    for pattern, role in ROLE_KEYWORDS:
        if re.search(pattern, t):
            return role
    return None


def scopes_for(title: str | None, industries: list[str] | None) -> list[str]:
    scopes = ["global"]
    role = role_of(title)
    if role:
        scopes.append(f"role:{role}")
    scopes += [f"industry:{i.strip().lower()}" for i in (industries or [])[:3] if i and i.strip()]
    return scopes


def relevant(title: str | None, industries: list[str] | None, limit: int = 6) -> list[dict]:
    """Guidance for this recipient, best outcome first."""
    return db.feedback_for(scopes_for(title, industries), limit) or []


def exemplars(title: str | None, limit: int = 2) -> list[dict]:
    """Winning emails, same role first."""
    return db.exemplars_for(role_of(title), limit) or []


def add(guidance: str, scope: str = "global", source: str = "human", score: float = 0.8,
        evidence: dict | None = None) -> int | None:
    return db.add_feedback(source, scope, guidance.strip(), max(0.0, min(1.0, score)), evidence)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="add a piece of guidance")
    a.add_argument("guidance")
    a.add_argument("--scope", default="global", help="global | role:<role> | industry:<industry>")
    a.add_argument("--score", type=float, default=0.8, help="0..1, how strongly outcomes back it")
    sub.add_parser("list", help="show the repo, best first")
    args = ap.parse_args()
    if not db.enabled():
        raise SystemExit("NEON_DATABASE_URL is needed: the feedback repo lives there")
    if args.cmd == "add":
        print("added feedback", add(args.guidance, args.scope, "human", args.score))
    else:
        for f in db.all_feedback() or []:
            flag = "" if f["active"] else " (inactive)"
            print(f"#{f['id']:<4} {f['outcome_score']:.2f}  {f['scope']:<22} {f['source']:<7} {f['guidance']}{flag}")


if __name__ == "__main__":
    main()
