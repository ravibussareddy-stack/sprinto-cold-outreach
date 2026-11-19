"""Rules for re-engaging dormant companies (kept separate so they can be tested)."""
import re

# Certificate names vary ("SOC 2 Type II", "SOC-2 Type 2", "ISO/IEC 27001:2022"). Compare families,
# or a renamed badge looks like a new certificate and wakes a dormant company up.
FAMILIES = [(r"soc[\s\-]?(2|ii|3)\b", "SOC 2"), (r"soc[\s\-]?(1|i)\b", "SOC 1"), (r"27001", "ISO 27001"),
            (r"27701", "ISO 27701"), (r"27017", "ISO 27017"), (r"27018", "ISO 27018"), (r"42001", "ISO 42001"),
            (r"9001", "ISO 9001"), (r"hipaa", "HIPAA"), (r"pci", "PCI DSS"), (r"hitrust", "HITRUST"),
            (r"fedramp", "FedRAMP"), (r"csa star", "CSA STAR"), (r"cyber essentials", "Cyber Essentials"),
            (r"tisax", "TISAX"), (r"gdpr", "GDPR")]


def family(name: str) -> str:
    for pat, fam in FAMILIES:
        if re.search(pat, name or "", re.I):
            return fam
    return (name or "").strip()


def families(names) -> list[str]:
    return sorted({family(n) for n in names or [] if n})


def reactivation_new_certs(prev_record: dict | None, now_certs: list[str]) -> list[str]:
    """New certificates that justify re-engaging a dormant company.
    Only applies when the last run was DORMANT, and only while a gap remains
    (a company holding both SOC 2 and ISO 27001 has nothing to sell into)."""
    prev_q = (prev_record or {}).get("qualification", {})
    if prev_q.get("decision") not in ("DORMANT", "REACTIVATED"):
        return []
    now, before = set(families(now_certs)), set(families(prev_q.get("certs_held", [])))
    new = now - before
    if not new or {"SOC 2", "ISO 27001"} <= (now | before):      # certificates seen last time still count
        return []
    return sorted(new)


def reactivation(prev_record: dict | None, now_certs: list[str]) -> str:
    """Convenience wrapper used by the tests: returns the decision for a dormant company."""
    return "REACTIVATED" if reactivation_new_certs(prev_record, now_certs) else "DORMANT"
