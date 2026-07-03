# File Map — Automated Gov Contracts

> What each file does, its dependencies, and key notes. Last updated: 2026-07-03.

## Root

| File | Purpose | Notes |
|------|---------|-------|
| `dashboard.py` | Streamlit web app for live contract searching, filtering, QC analysis, CSV export | 28KB, brand-styled, uses sidebar filters + pagination |
| `requirements.txt` | Python deps: streamlit, requests, pandas, beautifulsoup4, lxml | Missing: `anthropic` (needed for drafting) |
| `CLAUDE.md` | Project instructions for Claude Code | Defines folder structure, data sources, org context |
| `.gitignore` | Ignores: pycache, venv, credentials, raw/processed data | `data/raw/*` and `data/processed/*` are gitignored |
| `cloudflared.exe` | Cloudflare tunnel executable | 65MB binary, untracked, likely for dashboard tunneling |

## src/scrapers/

| File | Purpose | Key Functions | Notes |
|------|---------|---------------|-------|
| `contracts_finder.py` | Fetches from Contracts Finder V2 REST API | `fetch_notices()`, `fetch_full_notice()`, `_normalise()` | POST API, retry logic, HTML fallback scraping |
| `find_a_tender.py` | Scrapes Find a Tender via web form POST | `fetch_notices()`, `validate_results()`, `_parse_results_page()` | No API available, HTML scraping, CSRF handling, batches keywords (max 15/query) |

## src/analysis/

| File | Purpose | Key Functions | Notes |
|------|---------|---------------|-------|
| `qc_agents.py` | 4 QC agents: keyword relevance, summary quality, data completeness, duplicates | `run_all_qc()`, `audit_keyword_relevance()`, `audit_summary_quality()`, `audit_data_completeness()`, `audit_duplicates()` | Fuzzy matching via SequenceMatcher, 27 core AI/data terms |
| `fit_scoring.py` | Rule-based 1-5 scoring for contract fit | `score_contract()`, `score_all()` | Config-driven (fit_scoring.json), strong/moderate/weak signals, value range bonuses |
| `requirement_extractor.py` | Parses contract notices to extract requirements | `extract_requirements()`, `_identify_sections()`, `_extract_criteria()` | 9 question categories, award weighting extraction, document/constraint detection |

## src/notifications/

| File | Purpose | Key Functions | Notes |
|------|---------|---------------|-------|
| `weekly_report.py` | Core weekly pipeline: fetch, filter, score, QC, email | `main()`, `fetch_and_filter()`, `build_email_html()`, `send_email_with_retry()` | 695 lines, MS Graph OAuth2, brand-styled HTML email, metric cards |
| `generate_snapshot.py` | Fetch + save snapshot without emailing | `main()` | Utility for testing/debugging |

## src/drafting/

| File | Purpose | Key Functions | Notes |
|------|---------|---------------|-------|
| `drafter.py` | Claude-powered tender response drafting | `draft_full_application()`, `draft_section()`, `detect_application_type()` | Uses Claude Sonnet 4, loads templates + reusable content |
| `run_draft.py` | End-to-end CLI: contract# -> fetch -> extract -> draft -> email | `run()`, `main()` | CLI args: --contract, --week, --test, --template |
| `send_draft.py` | Formats draft as branded HTML, sends via MS Graph | `build_draft_email()`, `send_draft()` | Confidence indicators, reusable flags, word counts |

## src/tracking/

| File | Purpose | Key Functions | Notes |
|------|---------|---------------|-------|
| `store.py` | Persists contract status across runs | `merge_results()`, `update_contract_status()`, `get_pipeline_contracts()`, `save_weekly_snapshot()`, `resolve_contract_number()` | JSON file storage, status lifecycle (new->seen->shortlisted->drafting->submitted->won/lost), keeps 8 snapshots |

## config/

| File | Purpose | Key Values |
|------|---------|------------|
| `search_criteria.json` | Keywords (41), CPV codes (13), date range, value filters | 90-day lookback, min £25k, stages: tender+planning |
| `email_criteria.json` | Email recipients, value cap, sorting | Recipients: marta@inferencegroup.com, max £500k, open_only |
| `fit_scoring.json` | Scoring weights, signals, value ranges, buyer bonuses | Ideal: £25k-£250k, strong AI/data signals, sector bonuses/penalties |
| `application_types.json` | 4 procurement types + auto-detection rules | open_procedure (default), restricted_sq, restricted_itt, below_threshold |

## templates/

| File | Purpose | Sections |
|------|---------|----------|
| `open_procedure.json` | Single-stage full tender | 10 sections (company, experience, methodology, team, social value, QA, risk, mobilisation, innovation, pricing) |
| `restricted_sq.json` | Stage 1 shortlist questionnaire | 7 sections (company info, exclusions, financial, technical, team, QA, policies) |
| `restricted_itt.json` | Stage 2 detailed proposal | 9 sections (understanding, methodology, experience, team, social value, risk, mobilisation, innovation, pricing) |
| `below_threshold.json` | Simplified proposal (<£213k) | 7 sections (about, needs, approach, why us, team, pricing, social value) |

## templates/reusable/

| File | Purpose | TODOs |
|------|---------|-------|
| `company_overview.md` | Standard company intro (~14 staff, core services, sectors) | Companies House #, VAT, DUNS, certifications |
| `team_profiles.md` | Richard Davis + Tim Linsell bios | More team members needed |
| `social_value.md` | PPN 06/20 aligned commitments | Measurable targets need confirming |
| `quality_assurance.md` | QA + infosec approach | Cyber Essentials/ISO 27001 status unconfirmed |
| `policies.md` | 8 policy references | Insurance levels, ICO registration # |
| `case_studies/*.md` | 3 case studies (Ofcom, Lloyds, REPL Group) | All founder experience, not IG contracts |

## scripts/

| File | Purpose | Notes |
|------|---------|-------|
| `get_ms_token.py` | Obtain MS Graph refresh token | One-time auth helper, device code flow |
| `preview_template.py` | Render templates as styled HTML | Usage: `python -m scripts.preview_template --all` |

## .github/workflows/

| File | Trigger | What it does |
|------|---------|--------------|
| `weekly-contracts.yml` | Friday 7:30 UTC + manual | Runs weekly_report.py, commits tracking, alerts on failure |
| `reminders.yml` | 1st/21st monthly + Oct/Mar | Token renewal + clock change reminders |
| `draft-application.yml` | Manual only | Runs run_draft.py with contract #, commits tracking |

## data/

| Path | Purpose | Notes |
|------|---------|-------|
| `data/tracking/contracts.json` | Persistent contract pipeline | 7 contracts tracked (5 expired as of 2026-07-03) |
| `data/processed/weekly_*.json` | Weekly snapshots (max 8 kept) | 1 snapshot exists: 2026-04-20 (8 contracts) |
| `data/raw/` | Raw scraped data | Empty (.gitkeep only) |
| `data/*.html` | Email/template previews | Preview files for review |
