# Benchmark on fresh and edge-case companies
Run 2026-11-19 10:47 against `https://sprinto-cold-outreach-web.vercel.app/api/run_case` (full pipeline, `send: false`). Expected outcomes are written in `benchmark.py`; two were set or corrected after a check, as each case notes.

**10/10 as expected.**

| Case | Expected | Actual | Certification | Time | Right action? |
|---|---|---|---|---|---|
| Certified company (PostHog: SOC 2 Type 2 on its security page, uses Drata) | DORMANT: no email | HTTP 200 · DORMANT · DORMANT | confirmed (SOC 2), uses Drata | 19.0s | yes |
| Small B2B SaaS, no security page, contact supplied (SalesRobot) | QUALIFIED / QUALIFIED_FLAGGED: draft an email | HTTP 200 · QUALIFIED_FLAGGED · DRAFTED<br>subject: *SalesRobot's outreach automation and data security*<br>*Flagged: no security page, so certification is unknown and greenfield points are withheld. Expectation set after a local qualification check.* | unknown | 79.7s | yes |
| Already chose a compliance platform (Hanko: ISO 27001 in progress with Comp AI) | DISQUALIFIED: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED<br>*Earlier expected a flagged draft; corrected after the audit: a company mid-audit on another platform gets no urgency points and scores 30, below the review score.* | in_progress, uses Comp AI | 17.1s | yes |
| Acquired company (Rows: 'Rows joined Superhuman'; its /security page is a 404) | DISQUALIFIED: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED | unknown | 16.9s | yes |
| Certified company, domain only (Kylas: 'ISO 27001:2022 certified' on its security page) | DORMANT: no email | HTTP 200 · DORMANT · DORMANT<br>*First written as QUALIFIED; corrected after the run showed its security page claims ISO 27001:2022. The earlier QUALIFIED result had the same bug as PostHog. Dormant before contact lookup, so no Hunter credit is spent.* | confirmed (ISO 27001) | 17.7s | yes |
| Acquired, little evidence (Qandle: now part of MYND, no security page) | DISQUALIFIED / MANUAL_REVIEW: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED | unknown | 8.5s | yes |
| Poor fit: not B2B SaaS / public (Zomato) | DISQUALIFIED / DORMANT: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED | unknown | 7.5s | yes |
| Unreachable site | FAILED: stop with site_unreachable | HTTP 200 · FAILED · FAILED · `site_unreachable` | - | 0.8s | yes |
| Bad input: contact.first_name is a number | invalid_contact: 400 with the field path, nothing runs | HTTP 400 · - · - · `invalid_contact` | - | 0.3s | yes |
| Bad input: malformed domain | invalid_domain: 400, nothing runs | HTTP 400 · - · - · `invalid_domain` | - | 0.3s | yes |
