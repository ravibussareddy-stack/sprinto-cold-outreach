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
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import frameworks
import questions
import rubric
import scraper
from extract import extract_profile
from pipeline_rules import family
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
NOT_A_TRIGGER = re.compile(r"acquir|acquisition|merg|part of|subsidiar|rebrand", re.I)
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
    badges = questions.ruling("badge_only") != "clear_only"      # your ruling: do bare badges count?
    for pg in pages:
        c = page_certs(pg)
        names |= set(c["self_claimed"]) | (set(c.get("unclear", [])) if badges else set())
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
    held = {family(c) for c in certs_held(p, pages)
            if c.lower() not in excluded and family(c).lower() not in excluded and COMPLIANCE_CERT.search(c)}
    ev = all_evidence(pages)
    if "SOC 2" in held and questions.ruling("soc2_type1_only") == "prospect" and questions.soc2_type1_only(ev):
        held.discard("SOC 2")                                     # your ruling: Type I alone is not certified
    if questions.ruling("old_report") == "lapsed" and questions.old_report(ev):
        held -= {"SOC 2", "ISO 27001"}                            # your ruling: old reports have lapsed
    return held


def all_evidence(pages: list[dict]) -> list[dict]:
    return [e | {"url": pg["url"]} for pg in pages for e in page_certs(pg).get("evidence", [])]


def short_circuit(p: CompanyProfile, pages: list[dict]) -> tuple[str, str] | None:
    """Lecture rule (Sprinto evidence map): stop at the first answer that ends the
    assessment. Returns (decision, reason) or None."""
    if p.is_b2b_saas is False:
        return "DISQUALIFIED", "Not a B2B SaaS company - outside Sprinto's ICP."
    certs = named_certs(p, pages)
    rule = rubric.get("certifications", "dormant_rule")
    core = {"SOC 2", "ISO 27001"} & certs
    dormant = (rule == "soc2_or_iso" and core) or (rule == "any_named_cert" and certs) or \
              (rule == "soc2_and_iso" and holds_cert(p, pages, "SOC 2") and holds_cert(p, pages, "ISO 27001"))
    if dormant:
        return ("DORMANT",
                f"Already certified ({', '.join(sorted(certs))}). Dormant: re-engage only if a new "
                f"relevant certificate appears.")
    stop = ineligible(p)
    if stop:
        return stop
    if compliance_tools(p, pages) and questions.ruling("uses_platform") == "skip":
        return "DISQUALIFIED", f"Already uses {compliance_tools(p, pages)[0]['tool']} and holds no certificate: you ruled to skip these."
    what = " ".join([p.one_line_description or "", p.buyer_type or ""] + p.target_industries)
    if questions.SELLS.search(what) and questions.ruling("sells_compliance") == "skip":
        return "DISQUALIFIED", "Sells compliance software itself: you ruled these are competitors."
    return None


def ineligible(p: CompanyProfile) -> tuple[str, str] | None:
    """Rules that hold whatever the certificates say: acquired, public or too big."""
    if (p.acquired_by or p.funding_stage == "Acquired") and questions.ruling("acquired") == "skip":
        return ("DISQUALIFIED", f"Now part of {p.acquired_by or 'another company'}: compliance is usually decided "
                                f"by the parent company's security team, so this is not an independent buyer.")
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


def deep_cert_urls(homepage: dict, tried: set[str], limit: int = 4) -> list[str]:
    """Certification still unknown after the normal loop: research harder instead of asking.
    The site's own sitemap first (pages that really exist), then the usual trust / security addresses."""
    u = urlparse(homepage["url"])
    base, host = f"{u.scheme}://{u.netloc}", u.netloc.replace("www.", "")
    found = scraper.sitemap_urls(base, r"trust|security|compliance|soc-?2|iso-?27001|certification")
    guesses = [f"{u.scheme}://trust.{host}", base + "/trust-center", base + "/trust", base + "/security",
               base + "/legal/security", base + "/compliance", base + "/security-and-compliance",
               base + "/privacy-policy", base + "/privacy"]          # privacy policies often name certifications
    out = []
    for c in found + guesses:
        if c not in tried and c.rstrip("/") not in tried and c not in out:
            out.append(c)
    return out[:limit]


def funding_urls(homepage: dict, tried: set[str], limit: int = 2) -> list[str]:
    """Funding is published in press, news, about and blog posts - not on careers or pricing pages.
    The site's sitemap first (an announcement post), then the usual press / about addresses."""
    u = urlparse(homepage["url"])
    base = f"{u.scheme}://{u.netloc}"
    found = [x for x in scraper.sitemap_urls(base, r"rais|funding|series-?[a-e]\b|seed-round|investment|investor|backed|announc", limit=6)
             if "career" not in x and "job" not in x]
    links = [l["url"] for l in homepage.get("links", []) if urlparse(l["url"]).netloc.replace("www.", "") == u.netloc.replace("www.", "")
             and re.search(r"/(press|news|newsroom|media|about|about-us|company|investors?)/?$", urlparse(l["url"]).path, re.I)]
    guesses = [base + p for p in ("/press", "/news", "/newsroom", "/about", "/about-us", "/company")]
    out = []
    for c in found + links + guesses:
        if c not in tried and c.rstrip("/") not in tried and c not in out:
            out.append(c)
    return out[:limit]


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
    regex_in_progress = any(page_certs(pg).get("in_progress") for pg in pages)
    tools = compliance_tools(p, pages)
    if (p.certification_in_progress or regex_in_progress) and tools:
        why.append(f"+0 certification in progress, but already with {tools[0]['tool']}: the platform is chosen, so no urgency points")
    elif p.certification_in_progress or regex_in_progress:
        cp += cpv["certification_in_progress"]; why.append(f"+{cpv['certification_in_progress']} certification explicitly in progress (actively preparing for audit)")
    # Acquisitions and mergers are not buying triggers: the parent's security team usually takes over.
    triggers = [t for t in p.recent_triggers if not NOT_A_TRIGGER.search(t)]
    if triggers:
        cp += cpv["recent_trigger"]; why.append(f"+{cpv['recent_trigger']} recent trigger: {triggers[0]}")
    cp = min(cp, cpv["cap"]); pts += cp

    # Certification gap. Greenfield points need evidence of absence (a security / trust page read
    # with no claim), never just "nothing found on the pages we happened to read".
    has_soc2, has_iso = holds_cert(p, pages, "SOC 2"), holds_cert(p, pages, "ISO 27001")
    status, tools = cert_status(p, pages), compliance_tools(p, pages)
    if p.expansion_frameworks and (has_soc2 or has_iso):
        pts += gap["new_framework"]
        why.append(f"+{gap['new_framework']} expansion: already certified, but needs {p.expansion_frameworks[0]}, now in your portfolio")
    elif tools:
        why.append(f"+0 certification gap: already uses {tools[0]['tool']} for compliance (a switch conversation, not greenfield)")
    elif not has_soc2 and not has_iso and status == "unknown":
        why.append("+0 certification gap: status unknown - no security or trust page found, so greenfield points are withheld")
    elif not has_soc2 and not has_iso and status == "confirmed":
        others = ", ".join(sorted(named_certs(p, pages)))
        pts += gap["no_soc2_or_iso"]; why.append(f"+{gap['no_soc2_or_iso']} holds {others} but no SOC 2 / ISO 27001 yet "
                                                  f"(a compliance programme exists; Sprinto adds the core frameworks)")
    elif not has_soc2 and not has_iso:
        read = [pg["url"] for pg in pages if is_security_page(pg["url"])]
        where = f"claimed on {read[0] if read else 'its security page'}"
        pts += gap["no_soc2_or_iso"]; why.append(f"+{gap['no_soc2_or_iso']} no SOC 2 / ISO 27001 {where} (greenfield)")
    elif not (has_soc2 and has_iso):
        pts += gap["holds_one"]; why.append(f"+{gap['holds_one']} holds {'SOC 2' if has_soc2 else 'ISO 27001'} only - expansion to "
                                            f"{'ISO 27001' if has_soc2 else 'SOC 2'} + continuous monitoring")
    return pts, why


def explain_fetch_error(code: str | None) -> str:
    """Scraper error code -> a sentence a person can act on (the code stays in brackets)."""
    c = code or ""
    if c.startswith("request_failed"):
        text = "The website could not be reached (the domain may not exist or the server did not answer)"
    elif c.startswith("http_4"):
        text = "The website refused or could not find the page"
    elif c.startswith("http_5"):
        text = "The website returned a server error"
    elif c == "disallowed_by_robots_txt":
        text = "The website's robots.txt does not allow automated reading"
    elif c == "not_found_page":
        text = "The homepage is a 'page not found' page"
    elif c == "no_usable_text":
        text = "The homepage loaded but had too little readable text"
    else:
        text = "The homepage could not be used"
    return f"{text} [{c or 'unknown'}]."


def _stop(profile: CompanyProfile, pages: list[dict]) -> tuple[str, str] | None:
    stop = short_circuit(profile, pages)
    if stop and stop[0] == "DORMANT":
        # Certified, but a framework you added to the portfolio fits them: an expansion prospect.
        exp = frameworks.expansions(profile.model_dump(), sorted(named_certs(profile, pages)))
        profile.expansion_frameworks = [f"{e['name']} ({e['reason']})" for e in exp]
        if exp:
            stop = ineligible(profile)          # an expansion still has to be a company Sprinto can sell to
    return stop


STAGE_LABEL = {"Pre-seed": "early (pre-seed / seed)", "Seed": "early (pre-seed / seed)", "Series A": "growth (Series A-C)",
               "Series B": "growth (Series A-C)", "Series C": "growth (Series A-C)", "Series D+": "late (Series D+)",
               "Public": "public", "Acquired": "acquired", "Bootstrapped": "bootstrapped"}


def _band(n: int | None) -> str | None:
    if not n:
        return None
    for hi, label in ((10, "1-10"), (50, "11-50"), (200, "51-200"), (500, "201-500"), (1000, "501-1,000")):
        if n <= hi:
            return label
    return "1,000+"


def firmographics(profile, pages: list[dict], trace: list[dict]) -> dict:
    """Startup stage, funding and headcount as explicit fields, each with its quote and source page,
    and where the run looked - so "not found" is explained, not silent."""
    ev = {e.field: e for e in (profile.evidence or [])}
    fund = ev.get("funding_stage") or ev.get("funding_evidence")
    emp = ev.get("employee_estimate")
    looked = [t["fetch"] for t in trace if t.get("ok") and (t.get("for", "").startswith("stage") or "for" not in t)]
    return {
        "startup_stage": STAGE_LABEL.get(profile.funding_stage) or "not found",
        "funding": {"round": profile.funding_stage or "not found",
                    "evidence": (fund.quote if fund else profile.funding_evidence) or None,
                    "source": fund.source_url if fund else None},
        "employees": {"estimate": profile.employee_estimate, "band": _band(profile.employee_estimate),
                      "evidence": emp.quote if emp else None, "source": emp.source_url if emp else None},
        "open_roles": profile.open_roles_count,
        "looked_at": looked,
    }


def judgement(profile, pages) -> list[dict]:
    """Situations here that the rules leave to your judgement (questions.TOPICS + new ones)."""
    core = {"SOC 2", "ISO 27001"} & {family(c) for c in certs_held(profile, pages)}
    return questions.detect(profile, all_evidence(pages), core, compliance_tools(profile, pages))


def _pending_flags(findings: list[dict]) -> list[str]:
    out = []
    for f in findings:
        if f["topic"] in questions.TOPICS and not questions.answered(f["topic"]):
            t = questions.TOPICS[f["topic"]]
            label = next(o["label"] for o in t["options"] if o["value"] == t["default"])
            out.append(f"judgement call waiting for you: {t['title'].lower()} ({f['finding']}); using the default: {label}")
    return out


def _early(stop, profile, pages, trace) -> dict:
    decision, reason = stop
    findings = judgement(profile, pages)
    ruled = questions.apply_open_ruling(profile, decision, reason, findings)
    if ruled:
        decision, reason = ruled
    return {"decision": decision, "reason": reason, "score": None, "score_reasons": [],
            "certs_held": sorted(named_certs(profile, pages)), "flags": _pending_flags(findings),
            "firmographics": firmographics(profile, pages, trace),
            "certification": cert_evidence(profile, pages), "judgement": findings,
            "profile": profile, "pages": pages, "trace": trace}


def qualify(domain: str, log) -> dict:
    """The evidence loop: homepage, then rounds of parallel fetches (one page per missing question)."""
    cap = rubric.get("evidence_loop", "max_extra_fetches")
    url = domain if domain.startswith("http") else f"https://{domain}"
    home = scraper.fetch(url)
    trace = [{"fetch": url, "ok": home["ok"], "method": home["method"], "error": home["error"]}]
    if not home["ok"]:
        return {"decision": "FAILED", "reason": explain_fetch_error(home["error"]), "trace": trace,
                "profile": None, "pages": []}

    pages, tried = [home], {home["url"], url}
    questions.load()                                   # your standing rulings, read once for this run
    try:                                               # funding news from the daily crawl counts as evidence
        import crawl
        for n in crawl.news_pages(domain):
            pages.append(n); tried.add(n["url"])
            trace.append({"fetch": n["url"], "for": "stage (news)", "ok": True, "method": n["method"], "error": None})
    except Exception:
        pass
    profile = extract_profile(domain, pages)
    log(f"  extracted from homepage; missing: {missing_facts(profile, pages) or 'nothing'}")

    extra = 0
    deep_done = False
    funding_done = False
    per_question = {"stage": 0, "certs": 0, "saas": 0}
    while True:
        stop = _stop(profile, pages)
        if stop:
            return _early(stop, profile, pages, trace)
        missing = missing_facts(profile, pages)
        if not missing or extra >= cap:
            break
        # One round: the best page for EACH missing question, fetched at the same time, then one
        # re-extraction. Certification first (it can end the assessment and moves the score across
        # the threshold), then stage, then SaaS. Each question keeps its own fetch limit.
        batch = []
        for cand in ("certs", "stage", "saas"):
            if cand not in missing or per_question[cand] >= rubric.get("evidence_loop", cand) or extra + len(batch) >= cap:
                continue
            nxt = pick_next_url(cand, home, tried | {u for u, _ in batch})
            if nxt:
                batch.append((nxt, cand))
        if "stage" in missing and not funding_done:
            # Funding rounds are announced in press / news / about posts: look there in this same round.
            funding_done = True
            for u in funding_urls(home, tried | {u for u, _ in batch}):
                batch.append((u, "stage (funding news)"))
        if "certs" in missing and per_question["certs"] >= rubric.get("evidence_loop", "certs") and not deep_done:
            # The usual security page gave nothing: research deeper in this same round (no extra wait).
            deep_done = True
            for u in deep_cert_urls(home, tried | {u for u, _ in batch}, limit=3):
                batch.append((u, "certs (deeper research)"))
        if not batch:
            break
        for u, q in batch:
            tried.add(u)
            if q in per_question:
                per_question[q] += 1
        with ThreadPoolExecutor(max_workers=len(batch)) as pool:
            fetched = list(pool.map(lambda b: scraper.fetch(b[0]), batch))
        got = False
        for (u, q), pg in zip(batch, fetched):
            extra += 0 if "(" in q else 1        # deeper research and funding news do not use the fetch budget
            trace.append({"fetch": u, "for": q, "ok": pg["ok"], "method": pg["method"], "error": pg["error"]})
            log(f"  loop {extra}/{cap}: need {q} -> {u} ({'ok' if pg['ok'] else pg['error']})")
            if pg["ok"]:
                pages.append(pg); got = True
        if got:
            profile = extract_profile(domain, pages)

    # Certification still unknown: research harder (sitemap, trust pages) rather than ask anyone.
    if "certs" in missing_facts(profile, pages) and not deep_done:
        deep = deep_cert_urls(home, tried)
        if deep:
            for u in deep:
                tried.add(u)
            with ThreadPoolExecutor(max_workers=len(deep)) as pool:
                fetched = list(pool.map(scraper.fetch, deep))
            got = False
            for u, pg in zip(deep, fetched):
                trace.append({"fetch": u, "for": "certs (deeper research)", "ok": pg["ok"], "method": pg["method"], "error": pg["error"]})
                log(f"  deeper research: {u} ({'ok' if pg['ok'] else pg['error']})")
                if pg["ok"]:
                    pages.append(pg); got = True
            if got:
                profile = extract_profile(domain, pages)
                stop = _stop(profile, pages)
                if stop:
                    return _early(stop, profile, pages, trace)

    still_missing = missing_facts(profile, pages)
    pts, reasons = score(profile, pages)
    flags = []
    certification = cert_evidence(profile, pages)
    if certification["compliance_tools"] and not profile.compliance_tool_used:
        profile.compliance_tool_used = certification["compliance_tools"][0]["tool"]   # the writer sees it too
    if "certs" in still_missing:
        flags.append("certification unknown (no security or trust page, even after the sitemap): discovery email that asks, never assumes")
    if profile.expansion_frameworks:
        flags.append(f"expansion prospect: already certified; pitch {profile.expansion_frameworks[0].split(' (')[0]}")
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
    profile.certification_status = certification["status"]          # the writer frames the email by it
    if profile.is_b2b_saas is None:
        decision = "MANUAL_REVIEW"
    elif certification["status"] == "unknown":
        # No certification information anywhere, even after the deeper research: just ask them.
        decision = "QUALIFIED_FLAGGED"
        reason = (f"score {pts}/100; no certification information found anywhere, so a discovery email asks "
                  f"whether SOC 2 or ISO 27001 is on their roadmap")
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
    findings = judgement(profile, pages)
    flags += _pending_flags(findings)
    ruled = questions.apply_open_ruling(profile, decision, reason, findings)
    if ruled:
        decision, reason = ruled
    return {"decision": decision, "score": pts, "score_reasons": reasons, "judgement": findings,
            "still_missing": still_missing, "flags": flags, "reason": reason,
            "certs_held": sorted(named_certs(profile, pages)), "certification": certification,
            "firmographics": firmographics(profile, pages, trace),
            "expansion": profile.expansion_frameworks,
            "profile": profile, "pages": pages, "trace": trace}
