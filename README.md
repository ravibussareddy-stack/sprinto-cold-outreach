# Sprinto - Cold Outreach, at Scale (MESA T2 · AI Case 2)

A repeatable 7-step pipeline. Give it a list of domains, and for each one it:

1. **Scrapes** the site (single-fetch, robots.txt respected, cached, Jina Reader fallback for JS-only/blocked pages)
2. **Extracts** a structured company profile with Gemini (named fields + verbatim evidence quotes)
3. **Qualifies** against Sprinto's ICP with an **agent loop**: code checks which facts are missing and fetches the specific page that answers them (max 4 extra fetches)
4. **Finds the PoC**: Hunter.io Domain Search (Option A) or a hard-coded Apollo lookup (Option B)
5. **Identifies two pain points** for that person, each grounded in a researched fact
6. **Drafts a personalised email** by first name with a specific hook, then checks it in code (length, name, CTA, no placeholders) and redrafts once if it fails
7. **Sends via Gmail** to **your own inbox** (hard-coded `SEND_TO`). The intended recipient goes in the footer

Every decision lands in `runs/<timestamp>/report.md` plus one JSON file per company, and in the Neon `runs` table. Scraped pages are cached in Neon for 7 days.

**Stack:** Gemini API (LLM) · Neon Postgres (page cache + run history) · Hunter.io or Apollo (contacts) · Gmail SMTP (send)

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in keys
cp poc_overrides.example.json poc_overrides.json   # only if using Apollo (Option B)
python pipeline.py domains.txt            # dry run: research + drafts, no send
python pipeline.py domains.txt --send     # send drafts to your own inbox
python pipeline.py --domain acme.io --send
```

Set `GEMINI_API_KEY` and `NEON_DATABASE_URL` in `.env`. The tables are created on first run.

Gmail needs an **App Password** (Google Account → Security → 2-Step Verification → App passwords).

## "Right companies": the ICP definition

Sprinto sells automated SOC 2 / ISO 27001 / GDPR compliance to SaaS companies. Buyers convert when **(a)** they sell B2B SaaS, **(b)** they're at the stage where enterprise deals start requiring a security report (Series A–C, ~20–500 people), and **(c)** they don't yet have the certification, or have one and need a second framework.

**Hard disqualifiers (short-circuit, stop early)**
- Not B2B SaaS
- Already holds **both** SOC 2 and ISO 27001 (no gap to sell into)
- Public company or >1,000 employees (in-house GRC team or enterprise GRC tooling)

**Score (0–100, computed in code so every point is explainable)**

| Bucket | Signal | Points |
|---|---|---|
| Stage fit (max 30) | Series A/B/C | 30 |
| | Seed | 15 |
| | Series D+ | 10 |
| | Unknown stage → 20–500 employees / ≥5 open roles | 20 / 15 |
| Compliance pressure (max 50) | Enterprise tier on pricing | 15 |
| | ≥2 named customers | 10 |
| | Enterprise features (SSO/SAML/audit logs/SLA) | 5 |
| | Sensitive data / regulated industries | 10 |
| | Hiring security/compliance roles | 10 |
| | Certification "in progress" | 10 |
| | Recent trigger (funding, expansion, big win) | 5 |
| Certification gap (max 20) | No SOC 2 / ISO claimed | 20 |
| | Holds one of the two (expansion) | 10 |

**≥50 and stage known → QUALIFIED.** 35–49, or stage still unknown after the loop → **QUALIFIED_FLAGGED** (lower rank, review before any real send). <35 → **DISQUALIFIED**. SaaS status unknown → **MANUAL_REVIEW**.

## Evidence map (drives the loop)

| Question | Sufficient when (checked in code) | Where it lives | Limit | If unanswered | Short-circuit |
|---|---|---|---|---|---|
| Already certified? | cert string found (incl. footer badges/alt text via regex) or trust page read | footer, /security, /trust | 1 | treat as not certified | both SOC 2 + ISO → stop |
| B2B SaaS? | `is_b2b_saas` set and buyer named | hero, /about, /product | 1 | manual review | not SaaS → stop |
| Right stage? | funding stage, ≥5 open roles, enterprise tier, or headcount | /careers (incl. Greenhouse/Lever/Ashby), /pricing, /customers | 2 | qualify flagged | Public/huge → stop |
| **Worst case** | 4 fetches beyond the homepage | | | | |

The model fills named fields. Code decides whether it knows enough, so a model that is confident about an empty page can't fool the loop.

## Who is the right person (step 4)

The PoC is whoever **owns getting certified** at that company size:
- **<50 people** → Founder/CEO or CTO (no security team yet; deals stuck in security review hit the CEO)
- **50–200** → Security/compliance lead if one exists, else CTO/VP Eng (engineering loses weeks to evidence collection)
- **>200** → CISO / Head of Security / GRC first, then CTO

Excluded: recruiting, sales, marketing, support, interns. Ties are broken by Hunter confidence. The other candidates are logged.

## Why the email is written this way (step 6)

- Opens on a **true, specific fact** from the research (funding round, enterprise tier, named customer, open security role), not flattery
- Links that fact to the person's #1 pain in one sentence, then gives Sprinto's concrete outcome
- 70–120 words, one question as the CTA, no buzzwords, no invented facts
- Code validates the draft before sending. A failed draft is redrafted once, then held back

## The 9 checks / boundary

The system researches and drafts. It never emails a prospect directly: `To:` is always your inbox. In production, the Growth Lead stays reviewer-and-sender. Kill criteria (from the lecture): Growth Lead rewrites >50% of emails, the system costs more time than it saves, or PoC accuracy drops below 60%.

## Cost

Pages are cleaned and capped at ~1,500 words before reaching the model. Each company costs roughly 3–6 Gemini calls. Cached pages (7 days in Neon) cost nothing on re-runs. Set `GEMINI_MODEL` in `.env` to switch models.
