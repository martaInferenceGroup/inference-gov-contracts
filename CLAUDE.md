# Automated Government Contracts

## Project Purpose
Reduce manual effort in finding UK government contract opportunities and speed up tender submission. Uses Claude for relevance filtering and application content drafting.

## Organisation
Inference Group (TCN Capital) — UK-based. Key stakeholder: Richard.

## Folder Structure
- `docs/` — Project brief, links, process documentation
  - `PROJECT_BRIEF.md` — Aims, goals, org profile, relevance criteria
  - `LINKS.md` — Tender portals, Notion resources, SharePoint references
  - `PROCESS.md` — End-to-end pipeline: scrape → store → filter → shortlist → draft → submit
- `src/scrapers/` — Scripts to pull tenders from Contracts Finder and Find a Tender
- `src/analysis/` — Filtering, scoring, and fit-assessment logic (Claude-powered)
- `src/notifications/` — Alerts for new matching contracts
- `config/` — Search criteria, API keys (gitignored), schedule settings
- `data/raw/` — Raw scraped tender data
- `data/processed/` — Filtered and enriched tender records
- `templates/` — Reusable bid/application templates and knowledge base content

## Key Data Sources
- Contracts Finder: https://www.contractsfinder.service.gov.uk/Search/Results
- Find a Tender: https://www.find-tender.service.gov.uk/Search
- Reusable Bid Information: Notion (needs audit — some content may be out of date)
- Historical tracking: SharePoint list (Anna's)
