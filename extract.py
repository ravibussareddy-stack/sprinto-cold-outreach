"""Step 2 - Extract structured company data from cleaned page text."""
from llm import ask
from schemas import CompanyProfile

SYSTEM = """You are a B2B research analyst. Extract facts about ONE company from
its own website text into the given schema.

Rules:
- Only use what the text supports. If a fact is not in the text, leave it null
  or empty. Never guess funding stage or headcount from vibes.
- certifications_claimed = certifications the company itself holds. "Helping
  customers become SOC 2 compliant" is NOT a claim that the company holds SOC 2.
- For each important field you fill, add an evidence quote (verbatim, short)
  with the source URL it came from."""


def extract_profile(domain: str, pages: list[dict]) -> CompanyProfile:
    blocks = []
    for p in pages:
        blocks.append(f"=== SOURCE: {p['url']} ===\n{p['text']}")
        if p["certs"]["mentioned"]:
            blocks.append(f"(Regex scan of raw HTML incl. footer/badges on {p['url']} "
                          f"found mentions of: {', '.join(p['certs']['mentioned'])}"
                          f"{'; language suggests certification IN PROGRESS' if p['certs']['in_progress'] else ''})")
    user = f"Company domain: {domain}\n\n" + "\n\n".join(blocks)
    # temperature 0: the same pages must give the same facts, or the score moves between runs
    return ask(CompanyProfile, SYSTEM, user, temperature=0.0)
