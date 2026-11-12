"""Step 3 - Qualify against Sprinto's ICP, with an agent loop.

The loop follows the L4 Evidence Map:

| Question          | Sufficient when                                   | Where it lives            | Short-circuit            |
|-------------------|---------------------------------------------------|---------------------------|--------------------------|
| Already certified?| a cert string is found, or a trust page was read  | footer, /security, /trust | holds SOC 2 AND ISO -> stop |
| B2B SaaS?         | is_b2b_saas set and buyer_type named              | hero, /product, /about    | not B2B SaaS -> stop     |
| Right stage?      | funding stage, >=5 open roles, enterprise tier,   | /careers, /pricing,       | Public / huge -> stop    |
|                   | or headcount                                      | /customers                |                          |

The MODEL extracts facts into named fields. The CODE checks which fields are
filled and decides what to fetch next. Hard cap: MAX_EXTRA_FETCHES.
"""
import re
from urllib.parse import urlparse

import config
import scraper
from extract import extract_profile
from schemas import CompanyProfile

# Which page answers which question, in priority order. Careers is rung one
# for stage: it answers headcount, seniority and often funding in one request.
PAGE_KEYWORDS = {
    "stage": ["careers", "jobs", "join-us", "work-with-us", "pricing", "plans", "customers", "case-studies"],
    "certs": ["trust", "security", "compliance"],
    "saas":  ["about", "product", "platform", "company"],
}
GUESS_PATHS = {
    "stage": ["/careers", "/pricing", "/customers"],
    "certs": ["/security", "/trust"],
    "saas":  ["/about", "/product"],
}
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "keka.com",
             "zohorecruit", "freshteam.com", "bamboohr.com", "recruitee.com")

REGULATED = re.compile(r"fintech|financ|bank|payment|lending|insur|health|medic|clinic|hr\b|payroll|"
                       r"legal|govern|education|edtech|security|identity", re.I)
ENTERPRISE_FEATURES = re.compile(r"sso|saml|scim|audit log|sla|data residency|dpa|role-based|rbac", re.I)


# ---------------------------------------------------------------- sufficiency
def certs_held(p: CompanyProfile, pages: list[dict]) -> set[str]:
    """Certifications the company claims: the model's list plus the regex scan of
    raw HTML (footer badges, image alt text). Both count, so the score does not
    depend on whether the model happened to copy a badge."""
    names = set(p.certifications_claimed)
    for pg in pages:
        names |= set(pg["certs"]["mentioned"])
    return names


def holds_cert(p: CompanyProfile, pages: list[dict], name: str) -> bool:
    key = name.lower().replace(" ", "")
    return any(key in c.lower().replace(" ", "") for c in certs_held(p, pages))


def missing_facts(p: CompanyProfile, pages: list[dict]) -> list[str]:
    """Evidence-map questions still unanswered (L4 map, Sprinto table). Pure code.
    certs:  a named certification string is present (regex or model)
    saas:   is_b2b_saas set, and the buyer is named
    stage:  funding stage named, >=5 open roles, or an enterprise tier on pricing.
            Headcount is a score signal only, not evidence for stage."""
    missing = []
    if not certs_held(p, pages):
        missing.append("certs")
    if p.is_b2b_saas is None or (p.is_b2b_saas and not p.buyer_type):
        missing.append("saas")
    stage_known = (p.funding_stage is not None
                   or (p.open_roles_count or 0) >= 5
                   or p.has_enterprise_tier is not None)
    if not stage_known:
        missing.append("stage")
    return missing


# GDPR is a regulation, not a certification, so it does not trigger the rule.
NOT_CERTIFICATIONS = {"gdpr"}


def named_certs(p: CompanyProfile, pages: list[dict]) -> set[str]:
    return {c for c in certs_held(p, pages) if c.lower() not in NOT_CERTIFICATIONS}


def short_circuit(p: CompanyProfile, pages: list[dict]) -> tuple[str, str] | None:
    """Lecture rule (Sprinto evidence map): stop at the first answer that ends the
    assessment. Returns (decision, reason) or None."""
    if p.is_b2b_saas is False:
        return "DISQUALIFIED", "Not a B2B SaaS company - outside Sprinto's ICP."
    certs = named_certs(p, pages)
    if certs:
        return ("DORMANT",
                f"Already certified ({', '.join(sorted(certs))}). Dormant: re-engage only if a new "
                f"relevant certificate appears.")
    if p.funding_stage in ("Public",) or (p.employee_estimate or 0) > config.MAX_EMPLOYEES:
        return "DISQUALIFIED", "Too large/public - typically runs an in-house GRC team or enterprise GRC tool."
    return None


# ---------------------------------------------------------------- link choice
def pick_next_url(question: str, homepage: dict, tried: set[str]) -> str | None:
    home_host = urlparse(homepage["url"]).netloc.replace("www.", "")
    for kw in PAGE_KEYWORDS[question]:
        for link in homepage["links"]:
            u = link["url"]
            host = urlparse(u).netloc.replace("www.", "")
            same_site = host.endswith(home_host) or (question == "stage" and any(a in host for a in ATS_HOSTS))
            if not same_site or u in tried:
                continue
            if kw in urlparse(u).path.lower() or kw in link["text"].lower() or (kw == "careers" and any(a in host for a in ATS_HOSTS)):
                return u
    base = f"{urlparse(homepage['url']).scheme}://{urlparse(homepage['url']).netloc}"
    for path in GUESS_PATHS[question]:
        if base + path not in tried:
            return base + path
    return None


# ---------------------------------------------------------------- scoring
def score(p: CompanyProfile, pages: list[dict]) -> tuple[int, list[str]]:
    """Deterministic 0-100 score. Every point has a stated reason."""
    pts, why = 0, []

    # Stage fit (max 30): Sprinto's sweet spot is Series A-C.
    st = p.funding_stage
    if st in ("Series A", "Series B", "Series C"):
        pts += 30; why.append(f"+30 stage: {st} (core ICP)")
    elif st == "Seed":
        pts += 15; why.append("+15 stage: Seed (early, but often first enterprise deals)")
    elif st == "Series D+":
        pts += 10; why.append("+10 stage: Series D+ (may already have GRC tooling)")
    elif p.employee_estimate:
        e = p.employee_estimate
        add = 20 if 20 <= e <= 500 else 10 if e >= config.MIN_EMPLOYEES else 0
        pts += add; why.append(f"+{add} stage proxy: ~{e} employees")
    elif (p.open_roles_count or 0) >= 5:
        pts += 15; why.append(f"+15 stage proxy: {p.open_roles_count} open roles (scaling)")
    else:
        why.append("+0 stage: unknown after loop (flagged)")

    # Compliance pressure (max 50)
    cp = 0
    if p.has_enterprise_tier:
        cp += 15; why.append("+15 enterprise tier on pricing (security reviews come with it)")
    if len(p.named_customers) >= 2:
        cp += 10; why.append(f"+10 named customers: {', '.join(p.named_customers[:4])}")
    feats = [f for f in p.enterprise_features if ENTERPRISE_FEATURES.search(f)]
    if feats:
        cp += 5; why.append(f"+5 enterprise features: {', '.join(feats[:4])}")
    if p.handles_sensitive_data or REGULATED.search(" ".join(p.target_industries + [p.one_line_description or ""])):
        cp += 10; why.append("+10 handles sensitive data / sells into regulated industries")
    if p.security_compliance_roles_hiring:
        cp += 10; why.append(f"+10 hiring security/compliance: {', '.join(p.security_compliance_roles_hiring[:3])}")
    regex_in_progress = any(pg["certs"]["in_progress"] for pg in pages)
    if p.certification_in_progress or regex_in_progress:
        cp += 10; why.append("+10 certification explicitly in progress (actively preparing for audit)")
    if p.recent_triggers:
        cp += 5; why.append(f"+5 recent trigger: {p.recent_triggers[0]}")
    cp = min(cp, 50); pts += cp

    # Certification gap (max 20)
    has_soc2, has_iso = holds_cert(p, pages, "SOC 2"), holds_cert(p, pages, "ISO 27001")
    if not has_soc2 and not has_iso:
        pts += 20; why.append("+20 no SOC 2 / ISO 27001 claimed (greenfield)")
    else:
        pts += 10; why.append(f"+10 holds {'SOC 2' if has_soc2 else 'ISO 27001'} only - expansion to "
                              f"{'ISO 27001' if has_soc2 else 'SOC 2'} + continuous monitoring")
    return pts, why


# ---------------------------------------------------------------- the loop
def qualify(domain: str, log) -> dict:
    url = domain if domain.startswith("http") else f"https://{domain}"
    home = scraper.fetch(url)
    trace = [{"fetch": url, "ok": home["ok"], "method": home["method"], "error": home["error"]}]
    if not home["ok"]:
        return {"decision": "FAILED", "reason": f"homepage unusable: {home['error']}", "trace": trace,
                "profile": None, "pages": []}

    pages, tried = [home], {home["url"], url}
    profile = extract_profile(domain, pages)
    log(f"  extracted from homepage; missing: {missing_facts(profile, pages) or 'nothing'}")

    extra = 0
    per_question = {"stage": 0, "certs": 0, "saas": 0}
    while True:
        stop = short_circuit(profile, pages)
        if stop:
            decision, reason = stop
            return {"decision": decision, "reason": reason, "score": None, "score_reasons": [],
                    "certs_held": sorted(named_certs(profile, pages)),
                    "profile": profile, "pages": pages, "trace": trace}
        missing = missing_facts(profile, pages)
        if not missing or extra >= config.MAX_EXTRA_FETCHES:
            break
        # Fetch for the highest-value missing fact; stage first (most decisive).
        # Each question has its own fetch limit from the evidence map.
        nxt, q = None, None
        for cand in ("stage", "certs", "saas"):
            if cand not in missing or per_question[cand] >= config.FETCH_LIMITS[cand]:
                continue
            nxt = pick_next_url(cand, home, tried)
            if nxt:
                q = cand
                break
        if not nxt:
            break
        per_question[q] += 1
        tried.add(nxt); extra += 1
        pg = scraper.fetch(nxt)
        trace.append({"fetch": nxt, "for": q, "ok": pg["ok"], "method": pg["method"], "error": pg["error"]})
        log(f"  loop {extra}/{config.MAX_EXTRA_FETCHES}: need {q} -> {nxt} ({'ok' if pg['ok'] else pg['error']})")
        if pg["ok"]:
            pages.append(pg)
            profile = extract_profile(domain, pages)

    still_missing = missing_facts(profile, pages)
    pts, reasons = score(profile, pages)
    flags = []
    if "certs" in still_missing:
        flags.append("certification status unanswered: treated as not certified")
    if "stage" in still_missing:
        flags.append("stage unanswered after the loop")
    # Evidence-map outcomes (Sprinto table, page 40):
    #   SaaS unknown                      -> manual_review
    #   stage unanswered                  -> qualify at lower rank, flagged
    #   any other question unanswered     -> flagged
    #   all answered and score >= QUALIFY -> qualified
    #   score below REVIEW_SCORE          -> disqualified
    if profile.is_b2b_saas is None:
        decision = "MANUAL_REVIEW"
    elif "stage" in still_missing or flags:
        decision = "QUALIFIED_FLAGGED"
    elif pts >= config.QUALIFY_SCORE:
        decision = "QUALIFIED"
    elif pts >= config.REVIEW_SCORE:
        decision = "QUALIFIED_FLAGGED"
    else:
        decision = "DISQUALIFIED"
    return {"decision": decision, "score": pts, "score_reasons": reasons,
            "still_missing": still_missing, "flags": flags, "reason": f"score {pts}/100",
            "certs_held": sorted(named_certs(profile, pages)),
            "profile": profile, "pages": pages, "trace": trace}
