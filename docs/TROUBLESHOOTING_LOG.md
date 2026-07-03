# Troubleshooting Log

> Running log of issues found, tests performed, and resolutions. Started 2026-07-03.

## Issues Found During Initial Audit (2026-07-03)

### ISSUE-001: 484 lines of uncommitted changes
- **Status:** OPEN
- **Severity:** High
- **Details:** 7 modified files + 12 untracked items not committed. This includes core functionality (weekly_report.py +292 lines, contracts_finder.py +125 lines, find_a_tender.py +45 lines) and entire new modules (drafting/, tracking/, analysis/fit_scoring.py, templates/).
- **Risk:** All Phase 2-3 work could be lost. GitHub Actions running old committed code, not the local version.
- **Action needed:** Review changes, commit, and push to activate new features in CI.

### ISSUE-002: Expired contracts not being cleaned from tracking
- **Status:** OPEN
- **Severity:** Medium
- **Details:** 5 of 7 contracts in `data/tracking/contracts.json` have past closing dates (Feb-May 2026). The `merge_results()` function in `store.py` auto-skips expired contracts on merge, but existing expired entries in "seen" status were never cleaned.
- **Action needed:** Either run a cleanup pass or add periodic expiry logic.

### ISSUE-003: Duplicate status history entries
- **Status:** OPEN
- **Severity:** Low
- **Details:** Contract 013737-2026 has 3 identical "Draft pipeline started" entries in status_history, suggesting the draft workflow was triggered multiple times.
- **Root cause:** Likely manual workflow re-runs without guard against duplicate status updates.
- **Action needed:** Add idempotency check in `update_contract_status()`.

### ISSUE-004: requirements.txt missing `anthropic` dependency
- **Status:** OPEN
- **Severity:** High (blocks drafting in CI)
- **Details:** `drafter.py` imports `anthropic` but `requirements.txt` only lists streamlit, requests, pandas, beautifulsoup4, lxml. The draft-application.yml workflow installs it separately, but requirements.txt should be the source of truth.
- **Action needed:** Add `anthropic >= 0.30.0` to requirements.txt.

### ISSUE-005: Reusable content has unconfirmed placeholders
- **Status:** OPEN
- **Severity:** High (for live tender submissions)
- **Details:** Multiple TODOs in reusable templates:
  - Companies House number, VAT, DUNS number
  - Cyber Essentials / ISO 27001 certification status
  - Insurance coverage levels and expiry dates
  - ICO registration number
- **Risk:** Submitting tenders with placeholder text will fail evaluations.
- **Action needed:** Gather real company data from Richard/team.

### ISSUE-006: No Inference Group case studies
- **Status:** OPEN
- **Severity:** Medium
- **Details:** All 3 case studies are founder experience at previous orgs (Ofcom, Lloyds, REPL Group). Since IG was founded Apr 2024, there may now be real delivery case studies to add.
- **Action needed:** Check if IG has completed any contracts and document them.

### ISSUE-007: MS Graph token requires manual renewal every ~30 days
- **Status:** KNOWN LIMITATION
- **Severity:** Medium
- **Details:** Reminder workflow sends emails on 1st/21st of each month. Token expiry causes silent email failures.
- **Workaround:** Reminders in place. No automated token refresh possible with current Azure app setup.

### ISSUE-008: Find a Tender has no full notice fetch
- **Status:** KNOWN LIMITATION
- **Severity:** Medium
- **Details:** `contracts_finder.py` has `fetch_full_notice()` for detailed contract data. Find a Tender scraper only uses search page data — no equivalent detail fetch. This limits requirement extraction for FaT contracts.
- **Impact:** Draft quality for FaT-sourced contracts will be lower.

### ISSUE-009: ANTHROPIC_API_KEY GitHub secret status unknown
- **Status:** OPEN
- **Severity:** High (blocks drafting pipeline)
- **Details:** `draft-application.yml` requires ANTHROPIC_API_KEY secret. Unknown if configured.
- **Action needed:** Verify secret exists in GitHub repo settings.

### ISSUE-010: Weekly email schedule doesn't auto-adjust for DST
- **Status:** KNOWN LIMITATION
- **Severity:** Low
- **Details:** Cron runs at UTC. BST/GMT switch causes email to arrive 1 hour early/late. Reminder workflow covers this but requires manual cron edit.

---

## Fixes Applied

### FIX-001: Duplicate NOTICE_URL constant (2026-07-03)
- **File:** `src/scrapers/contracts_finder.py`
- **Issue:** NOTICE_URL duplicated API_URL. Removed the duplicate.

### FIX-002: Idempotency guard for status history (2026-07-03)
- **File:** `src/tracking/store.py`
- **Issue:** `_update_status()` could add duplicate entries (seen with 013737-2026).
- **Fix:** Added check — if same status+date+by already exists in history, skip the append.

### FIX-003: Robust date normalisation (2026-07-03)
- **Files:** `src/notifications/weekly_report.py`, `src/tracking/store.py`
- **Issue:** FaT dates like "25 February 2026, 11:59pm" and ordinal dates ("1st March 2026") could fail parsing.
- **Fix:** Added `_normalise_date()` helper that handles ISO, UK long, time suffixes, and ordinal suffixes. Used consistently in expired-contract filter, urgency display, and tracking merge.

### FIX-004: Added anthropic to requirements.txt (2026-07-03)
- **File:** `requirements.txt`
- **Issue:** `anthropic` was imported by drafter.py but missing from deps.
- **Fix:** Added `anthropic>=0.30.0`.

### FIX-005: [REQUIRED] indicators in reusable content (2026-07-03)
- **Files:** `templates/reusable/company_overview.md`, `templates/reusable/policies.md`, `templates/reusable/quality_assurance.md`
- **Issue:** TODO comments were invisible in draft output.
- **Fix:** Replaced all `<!-- TODO -->` and `[Status to be confirmed]` with `[REQUIRED: ...]` tags. Drafter now detects these and shows a red warning banner in draft emails listing all missing fields.

### FIX-006: Non-critical steps wrapped in try/except (2026-07-03)
- **File:** `src/notifications/weekly_report.py`
- **Issue:** If fit scoring or QC flagging failed, entire email run crashed.
- **Fix:** Wrapped scoring and QC in try/except — email sends without those features if they fail.

### FIX-007: Tracking data cleanup (2026-07-03)
- **File:** `data/tracking/contracts.json`
- **Issue:** 6 expired contracts still in "seen"/"drafting" status. Duplicate status entries. Non-normalised dates.
- **Fix:** Marked 6 expired as "skipped", normalised all closing dates to ISO format, deduplicated 013737-2026 status history.

---

## Tests Performed

| # | Date | Test | Result | Notes |
|---|------|------|--------|-------|
| 1 | 2026-07-03 | Python syntax check on all 9 source files | PASS | `py_compile` — all files compile |
