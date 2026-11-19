"""Step 3 - Qualify against Sprinto's ICP, with an agent loop.

The loop follows the L4 Evidence Map:

| Question          | Sufficient when                                   | Where it lives            | Short-circuit            |
|-------------------|---------------------------------------------------|---------------------------|--------------------------|
| Already certified?| the company's own claim is found, or a security / | /security, /trust,        | any named cert -> dormant |
|                   | trust page was read (then "none found" is known)  | trust.<domain>, footer    | (rubric: dormant_rule)   |
| B2B SaaS?         | is_b2b_saas set and buyer_type named              | hero, /product, /about    | not B2B SaaS -> stop     |
| Right stage?      | funding stage, >=5 open roles, enterprise tier,   | /careers, /pricing,       | Public / huge -> stop    |
|                   | or headcount                                      | /customers                |                          |

The MODEL extracts facts into named fields. The CODE checks which fields are
filled and decides what to fetch next. Hard cap: MAX_EXTRA_FETCHES.
"""
import re
from urllib.parse import urlparse

import rubric
import scraper
from extract import extract_profile
from schemas import CompanyProfile

# Which page answers which question, in priority order. Careers is rung one
# for stage: it answers headcount, seniority and often funding in one request.
PAGE_KEYWORDS = {
    "stage": ["careers", "jobs", "join-us", "work-with-us", "pricing", "plans", "customers", "case-studies"],
    "certs": ["trust", "security", "compliance", "soc-2", "soc2"],
    "saas":  ["about", "product", "platform", "company"],
}
GUESS_PATHS = {
    "stage": ["/careers", "/pricing", "/customers"],
    "certs": ["/security", "/trust", "trust."],          # "trust." = the trust.<domain> subdomain
    "saas":  ["/about", "/product"],
}
ATS_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "workable.com", "keka.com",
             "zohorecruit", "freshteam.com", "bamboohr.com", "recruitee.com")

REGULATED = re.compile(r"fintech|financ|bank|payment|lending|insur|health|medic|clinic|hr\b|payroll|"
                       r"legal|govern|education|edtech|security|identity", re.I)
ENTERPRISE_FEATURES = re.compile(r"sso|saml|scim|audit log|sla|data residency|dpa|role-based|rbac", re.I)


# ---------------------------------------------------------------- sufficiency
def page_certs(pg: dict) -> dict:
    """The page's certification scan. Pages cached before mentions were attributed are re-scanned
    from their raw text; with no raw text, every mention counts (the old behaviour)."""
    c = pg.get("certs") or {}
    if pg.get("raw_text"):                         # always re-scan: cheap, and cached pages get today's rules
        return scraper.detect_certifications(pg["raw_text"])
    if "self_claimed" in c:
        return c
    return {**c, "self_claimed": list(c.get("mentioned", [])), "unclear": [], "evidence": [], "tools": []}


def is_security_page(url: str) -> bool:
    u = urlparse(url)
    return u.netloc.startswith("trust.") or any(k in u.path.lower() for k in ("trust", "security", "compliance", "soc"))


def certs_held(p: CompanyProfile, pages: list[dict]) -> set[str]:
    """Certifications the company itself claims: the model's list plus regex mentions in raw HTML
    that read as its own claim or as a bare badge (footer, alt text). Mentions about customers
    ("we help you get SOC 2") or vendors ("hosted on AWS, SOC 2 audited") never count."""
    names = set(p.certifications_claimed)
    for pg in pages:
        c = page_certs(pg)
        names |= set(c["self_claimed"]) | set(c.get("unclear", []))
    return names


def compliance_tools(p: CompanyProfile, pages: list[dict]) -> list[dict]:
    tools = [t | {"url": pg["url"]} for pg in pages for t in page_certs(pg).get("tools", [])]
    if p.compliance_tool_used and not any(t["tool"].lower() == p.compliance_tool_used.lower() for t in tools):
        tools.append({"tool": p.compliance_tool_used, "sentence": "stated in the extracted research", "url": None})
    return tools


def cert_status(p: CompanyProfile, pages: list[dict]) -> str:
    """confirmed: the company claims a certification. in_progress: says an audit is under way.
    none_found: a security / trust page was read and claims none. unknown: no such page read -
    absence of evidence, so it must not earn greenfield points."""
    if named_certs(p, pages):
        return "confirmed"
    if p.certification_in_progress or any(page_certs(pg).get("in_progress") for pg in pages):
        return "in_progress"
    if any(is_security_page(pg["url"]) for pg in pages):
        return "none_found"
    return "unknown"


def cert_evidence(p: CompanyProfile, pages: list[dict]) -> dict:
    return {"status": cert_status(p, pages),
            "held": sorted(named_certs(p, pages)),
            "security_pages_read": [pg["url"] for pg in pages if is_security_page(pg["url"])],
            "evidence": [e | {"url": pg["url"]} for pg in pages for e in page_certs(pg).get("evidence", [])][:8],
            "compliance_tools": compliance_tools(p, pages)}


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
    if cert_status(p, pages) == "unknown":
        missing.append("certs")
    if p.is_b2b_saas is None or (p.is_b2b_saas and not p.buyer_type):
        missing.append("saas")
    stage_known = (p.funding_stage is not None
                   or (p.open_roles_count or 0) >= 5
                   or p.has_enterprise_tier is not None)
    if not stage_known:
        missing.append("stage")
    return missing


# Security-compliance certifications Sprinto sells into or that show a compliance programme.
# Anything else the model calls a certification (FIDO2, a cloud partner badge) is not one here.
COMPLIANCE_CERT = re.compile(r"soc[\s\-]?(2|ii|1|i|3)\b|iso[\s/\-]?(iec[\s\-]?)?(27001|27701|27017|27018|42001|9001)|hipaa|"
                             r"pci[\s\-]?dss|hitrust|fedramp|csa star|cyber essentials|tisax|nist 800", re.I)


def named_certs(p: CompanyProfile, pages: list[dict]) -> set[str]:
    """Certifications that count for the dormant rule: security-compliance certificates only, minus
    rubric certifications.not_certifications (default: GDPR - a regulation, not a certification)."""
    excluded = {x.lower() for x in rubric.get("certifications", "not_certifications")}
    return {c for c in certs_held(p, pages) if c.lower() not in excluded and COMPLIANCE_CERT.search(c)}


def short_circuit(p: CompanyProfile, pages: list[dict]) -> tuple[str, str] | None:
    """Lecture rule (Sprinto evidence map): stop at the first answer that ends the
    assessment. Returns (decision, reason) or None."""
    if p.is_b2b_saas is False:
        return "DISQUALIFIED", "Not a B2B SaaS company - outside Sprinto's ICP."
    certs = named_certs(p, pages)
    rule = rubric.get("certifications", "dormant_rule")
    dormant = (rule == "any_named_cert" and certs) or \
              (rule == "soc2_and_iso" and holds_cert(p, pages, "SOC 2") and holds_cert(p, pages, "ISO 27001"))
    if dormant:
        return ("DORMANT",
                f"Already certified ({', '.join(sorted(certs))}). Dormant: re-engage only if a new "
                f"relevant certificate appears.")
    if p.funding_stage in ("Public",) or (p.employee_estimate or 0) > rubric.get("size", "max_employees"):
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
            if kw in urlparse(u).path.lower() or kw in link["text"].lower() or (kw == "careers" and any(a in host for a in ATS_HOSTS)) \
                    or (question == "certs" and host.startswith(kw)):
                return u
    base = f"{urlparse(homepage['url']).scheme}://{urlparse(homepage['url']).netloc}"
    for path in GUESS_PATHS[question]:
        cand = f"{urlparse(homepage['url']).scheme}://trust.{home_host}" if path == "trust." else base + path
        if cand not in tried:
            return cand
    return None


# ---------------------------------------------------------------- scoring
def score(p: CompanyProfile, pages: list[dict]) -> tuple[int, list[str]]:
    """Deterministic score. Every point has a stated reason; every value comes from the rubric."""
    sp, cpv, gap = rubric.load()["stage_points"], rubric.load()["compliance_points"], rubric.load()["certification_gap"]
    pts, why = 0, []

    # Stage fit: Sprinto's sweet spot is Series A-C.
    st = p.funding_stage
    if st in ("Series A", "Series B", "Series C"):
        pts += sp["series_a_c"]; why.append(f"+{sp['series_a_c']} stage: {st} (core ICP)")
    elif st == "Seed":
        pts += sp["seed"]; why.append(f"+{sp['seed']} stage: Seed (early, but often first enterprise deals)")
    elif st == "Series D+":
        pts += sp["series_d_plus"]; why.append(f"+{sp['series_d_plus']} stage: Series D+ (may already have GRC tooling)")
    elif p.employee_estimate:
        e = p.employee_estimate
        add = sp["headcount_20_500"] if 20 <= e <= 500 else \
            sp["headcount_other"] if e >= rubric.get("size", "min_employees") else 0
        pts += add; why.append(f"+{add} stage proxy: ~{e} employees")
    elif (p.open_roles_count or 0) >= 5:
        pts += sp["open_roles_5_plus"]; why.append(f"+{sp['open_roles_5_plus']} stage proxy: {p.open_roles_count} open roles (scaling)")
    else:
        why.append("+0 stage: unknown after loop (flagged)")

    # Compliance pressure (capped)
    cp = 0
    if p.has_enterprise_tier:
        cp += cpv["enterprise_tier"]; why.append(f"+{cpv['enterprise_tier']} enterprise tier on pricing (security reviews come with it)")
    if len(p.named_customers) >= 2:
        cp += cpv["named_customers"]; why.append(f"+{cpv['named_customers']} named customers: {', '.join(p.named_customers[:4])}")
    feats = [f for f in p.enterprise_features if ENTERPRISE_FEATURES.search(f)]
    if feats:
        cp += cpv["enterprise_features"]; why.append(f"+{cpv['enterprise_features']} enterprise features: {', '.join(feats[:4])}")
    if p.handles_sensitive_data or REGULATED.search(" ".join(p.target_industries + [p.one_line_description or ""])):
        cp += cpv["sensitive_data"]; why.append(f"+{cpv['sensitive_data']} handles sensitive data / sells into regulated industries")
    if p.security_compliance_roles_hiring:
        cp += cpv["security_hiring"]; why.append(f"+{cpv['security_hiring']} hiring security/compliance: "
                                                 f"{', '.join(p.security_compliance_roles_hiring[:3])}")
    regex_in_progress = any(pg["certs"]["in_progress"] for pg in pages)
    if p.certification_in_progress or regex_in_progress:
        cp += cpv["certification_in_progress"]; why.append(f"+{cpv['certification_in_progress']} certification explicitly in progress (actively preparing for audit)")
    if p.recent_triggers:
        cp += cpv["recent_trigger"]; why.append(f"+{cpv['recent_trigger']} recent trigger: {p.recent_triggers[0]}")
    cp = min(cp, cpv["cap"]); pts += cp

    # Certification gap. Greenfield points need evidence of absence (a security / trust page read
    # with no claim), never just "nothing found on the pages we happened to read".
    has_soc2, has_iso = holds_cert(p, pages, "SOC 2"), holds_cert(p, pages, "ISO 27001")
    status, tools = cert_status(p, pages), compliance_tools(p, pages)
    if tools:
        why.append(f"+0 certification gap: already uses {tools[0]['tool']} for compliance (a switch conversation, not greenfield)")
    elif not has_soc2 and not has_iso and status == "unknown":
        why.append("+0 certification gap: status unknown - no security or trust page found, so greenfield points are withheld")
    elif not has_soc2 and not has_iso:
        read = [pg["url"] for pg in pages if is_security_page(pg["url"])]
        pts += gap["no_soc2_or_iso"]; why.append(f"+{gap['no_soc2_or_iso']} no SOC 2 / ISO 27001 claimed on "
                                                  f"{read[0] if read else 'its security page'} (greenfield)")
    elif not (has_soc2 and has_iso):
        pts += gap["holds_one"]; why.append(f"+{gap['holds_one']} holds {'SOC 2' if has_soc2 else 'ISO 27001'} only - expansion to "
                                            f"{'ISO 27001' if has_soc2 else 'SOC 2'} + continuous monitoring")
    return pts, why


# ---------------------------------------------------------------- the loop
def qualify(domain: str, log, max_extra: int | None = None) -> dict:
    """max_extra caps the extra fetches below the rubric's limit (fast mode)."""
    cap = rubric.get("evidence_loop", "max_extra_fetches") if max_extra is None else \
        min(max_extra, rubric.get("evidence_loop", "max_extra_fetches"))
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
                    "certification": cert_evidence(profile, pages),
                    "profile": profile, "pages": pages, "trace": trace}
        missing = missing_facts(profile, pages)
        if not missing or extra >= cap:
            break
        # Fetch for the highest-value missing fact: certification first (it can end the assessment
        # and moves the score across the threshold), then stage, then SaaS.
        # Each question has its own fetch limit from the evidence map.
        nxt, q = None, None
        for cand in ("certs", "stage", "saas"):       # certs first: it can stop outreach outright
            if cand not in missing or per_question[cand] >= rubric.get("evidence_loop", cand):
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
        log(f"  loop {extra}/{cap}: need {q} -> {nxt} ({'ok' if pg['ok'] else pg['error']})")
        if pg["ok"]:
            pages.append(pg)
            profile = extract_profile(domain, pages)

    still_missing = missing_facts(profile, pages)
    pts, reasons = score(profile, pages)
    flags = []
    certification = cert_evidence(profile, pages)
    if certification["compliance_tools"] and not profile.compliance_tool_used:
        profile.compliance_tool_used = certification["compliance_tools"][0]["tool"]   # the writer sees it too
    if "certs" in still_missing:
        flags.append("certification status unknown: no security or trust page found; greenfield points withheld")
    for t in certification["compliance_tools"][:1]:
        flags.append(f"already uses {t['tool']} for compliance: pitch a switch, not a first audit")
    if "stage" in still_missing:
        flags.append("stage unanswered after the loop")
    # Evidence-map outcomes (Sprinto table, page 40):
    #   SaaS unknown                      -> manual_review
    #   stage unanswered                  -> qualify at lower rank, flagged
    #   any other question unanswered     -> flagged
    #   all answered and score >= QUALIFY -> qualified
    #   score below REVIEW_SCORE          -> disqualified
    reason = f"score {pts}/100"
    if profile.is_b2b_saas is None:
        decision = "MANUAL_REVIEW"
    elif pts < rubric.get("decision", "review_score"):
        # Too little evidence of fit to write. With questions still open a person should look;
        # with everything answered it is a clear no.
        if still_missing:
            decision = "MANUAL_REVIEW"
            reason = (f"score {pts}/100, below {rubric.get('decision', 'review_score')}, with "
                      f"{', '.join(still_missing)} unanswered: not enough evidence to write - review by hand")
        else:
            decision = "DISQUALIFIED"
    elif "stage" in still_missing or flags:
        decision = "QUALIFIED_FLAGGED"
    elif pts >= rubric.get("decision", "qualify_score"):
        decision = "QUALIFIED"
    elif pts >= rubric.get("decision", "review_score"):
        decision = "QUALIFIED_FLAGGED"
    else:
        decision = "DISQUALIFIED"
    return {"decision": decision, "score": pts, "score_reasons": reasons,
            "still_missing": still_missing, "flags": flags, "reason": reason,
            "certs_held": sorted(named_certs(profile, pages)), "certification": certification,
            "profile": profile, "pages": pages, "trace": trace}
