"""Rules for re-engaging dormant companies (kept separate so they can be tested)."""


def reactivation_new_certs(prev_record: dict | None, now_certs: list[str]) -> list[str]:
    """New certificates that justify re-engaging a dormant company.
    Only applies when the last run was DORMANT, and only while a gap remains
    (a company holding both SOC 2 and ISO 27001 has nothing to sell into)."""
    prev_q = (prev_record or {}).get("qualification", {})
    if prev_q.get("decision") not in ("DORMANT", "REACTIVATED"):
        return []
    now = set(now_certs)
    new = now - set(prev_q.get("certs_held", []))
    if not new or {"SOC 2", "ISO 27001"} <= now:
        return []
    return sorted(new)


def reactivation(prev_record: dict | None, now_certs: list[str]) -> str:
    """Convenience wrapper used by the tests: returns the decision for a dormant company."""
    return "REACTIVATED" if reactivation_new_certs(prev_record, now_certs) else "DORMANT"
