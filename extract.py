"""Step 2 - Extract structured company data from cleaned page text."""
from llm import ask
from schemas import CompanyProfile

SYSTEM = """You are a B2B research analyst. Extract facts about ONE company from
its own website text into the given schema.

Rules:
- Only use what the text supports. If a fact is not in the text, leave it null
  or empty. Never guess funding stage or headcount from vibes.
- certifications_claimed = certifications the company itself holds. "Helping
  customers become SOC 2 compliant" is NOT a claim that the company holds SOC 2,
  and neither is a vendor's certificate ("hosted on AWS, which is SOC 2 audited").
- For each important field you fill, add an evidence quote (verbatim, short)
  with the source URL it came from."""


def extract_profile(domain: str, pages: list[dict]) -> CompanyProfile:
    try:
        import questions
        known = questions.open_rulings()
    except Exception:
        known = []
    blocks = []
    for p in pages:
        blocks.append(f"=== SOURCE: {p['url']} ===\n{p['text']}")
        if p["certs"].get("evidence"):
            blocks.append(f"(Certification mentions found in raw HTML on {p['url']}, with who each is about: "
                          + "; ".join(f"{e['cert']} [{e['who']}]: \"{e['sentence'][:160]}\"" for e in p["certs"]["evidence"])
                          + (f"; compliance tool: {', '.join(t['tool'] for t in p['certs'].get('tools', []))}" if p["certs"].get("tools") else "")
                          + ")")
        elif p["certs"]["mentioned"]:
            blocks.append(f"(Regex scan of raw HTML incl. footer/badges on {p['url']} "
                          f"found mentions of: {', '.join(p['certs']['mentioned'])}"
                          f"{'; language suggests certification IN PROGRESS' if p['certs']['in_progress'] else ''})")
    user = f"Company domain: {domain}\n\n" + "\n\n".join(blocks)
    if known:
        user += ("\n\nunusual_finding topics already raised with the sales lead (reuse the topic name if one fits): "
                 + "; ".join(f"{k['topic']}: {k['question']}" for k in known[:20]))
    # temperature 0: the same pages must give the same facts, or the score moves between runs
    return ask(CompanyProfile, SYSTEM, user, temperature=0.0, thinking="low")   # copying facts: no deep reasoning needed
