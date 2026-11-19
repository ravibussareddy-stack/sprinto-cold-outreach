# Benchmark on fresh and edge-case companies
Run 2026-11-19 09:41 against `https://sprinto-cold-outreach-web.vercel.app/api/run_case` (fast mode unless the case says full, `send: false`). Expected outcomes are written in `benchmark.py`; two were set or corrected after a check, as each case notes.

**8/8 as expected.**

| Case | Expected | Actual | Certification | Time | Right action? |
|---|---|---|---|---|---|
| Certified company (PostHog: SOC 2 Type 2 on its security page, uses Drata) | DORMANT: no email | HTTP 200 · DORMANT · DORMANT | confirmed (SOC 2, SOC 2 Type 2), uses Drata | 28.2s | yes |
| Compliance work under way with another platform, contact supplied (Hanko: ISO 27001 in progress on its trust page, powered by Comp AI) | QUALIFIED_FLAGGED: draft an email | HTTP 200 · QUALIFIED_FLAGGED · DRAFTED<br>subject: *Hanko's in-progress ISO 27001 audit*<br>*Flagged: already uses a compliance platform, so it is a switch pitch reviewed before sending. Expectation set after a local qualification check.* | in_progress, uses Comp AI | 94.0s | yes |
| Certified company, domain only (Kylas: 'ISO 27001:2022 certified' on its security page) | DORMANT: no email | HTTP 200 · DORMANT · DORMANT<br>*First written as QUALIFIED; corrected after the run showed its security page claims ISO 27001:2022. The earlier QUALIFIED result had the same bug as PostHog. Dormant before contact lookup, so no Hunter credit is spent.* | confirmed (ISO 27001, ISO 27001:2022) | 17.2s | yes |
| Too little evidence to write (Qandle: no security page, score 20) | DISQUALIFIED / MANUAL_REVIEW: no email | HTTP 200 · MANUAL_REVIEW · MANUAL_REVIEW | unknown | 15.2s | yes |
| Poor fit: not B2B SaaS / public (Zomato) | DISQUALIFIED / DORMANT: no email | HTTP 200 · DISQUALIFIED · DISQUALIFIED | unknown | 5.7s | yes |
| Unreachable site | FAILED: stop with site_unreachable | HTTP 200 · FAILED · FAILED · `site_unreachable` | - | 0.8s | yes |
| Bad input: contact.first_name is a number | invalid_contact: 400 with the field path, nothing runs | HTTP 400 · - · - · `invalid_contact` | - | 0.3s | yes |
| Bad input: malformed domain | invalid_domain: 400, nothing runs | HTTP 400 · - · - · `invalid_domain` | - | 0.3s | yes |
