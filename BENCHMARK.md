# Benchmark on fresh and edge-case companies
Run 2026-11-19 08:36 against `https://sprinto-cold-outreach-web.vercel.app/api/run_case` (fast mode, `send: false`). Expected outcomes were written in `benchmark.py` before the run.

**5/7 as expected.**

| Case | Expected | Actual | Certification | Time | Right action? |
|---|---|---|---|---|---|
| Certified company (PostHog: SOC 2 Type 2 on its security page, uses Drata) | DORMANT: no email | HTTP 200 · DORMANT · DORMANT | confirmed (SOC 2, SOC 2 Type 2), uses Drata | 14.3s | yes |
| Qualified prospect, contact supplied (Kylas CRM) | QUALIFIED / QUALIFIED_FLAGGED: draft an email | HTTP 200 · DORMANT · DORMANT | confirmed (ISO 27001, ISO 27001:2022) | 17.6s | **no** |
| Qualified prospect, contact discovery finds no decision-maker (Kylas, domain only) | QUALIFIED / QUALIFIED_FLAGGED: stop with no_contact_found (Hunter lists only engineers) | HTTP 200 · DORMANT · DORMANT | confirmed (ISO 27001, ISO 27001:2022) | 16.5s | **no** |
| Poor fit: not B2B SaaS / public (Zomato) | DISQUALIFIED / DORMANT: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED | unknown | 10.5s | yes |
| Unreachable site | FAILED: stop with site_unreachable | HTTP 200 · FAILED · FAILED · `site_unreachable` | - | 0.8s | yes |
| Bad input: contact.first_name is a number | invalid_contact: 400 with the field path, nothing runs | HTTP 400 · - · - · `invalid_contact` | - | 0.3s | yes |
| Bad input: malformed domain | invalid_domain: 400, nothing runs | HTTP 400 · - · - · `invalid_domain` | - | 0.3s | yes |
