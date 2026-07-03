# Approach: Next Phase Features

**Date:** 20 April 2026
**Updated:** 20 April 2026
**Status:** Draft — awaiting approval before implementation

---

## Table of Contents

1. [Overview](#overview)
2. [Phase 1: Email Quality Improvements](#phase-1-email-quality-improvements)
3. [Phase 2: Compatibility Scoring](#phase-2-compatibility-scoring)
4. [Phase 3: Application Drafting Engine](#phase-3-application-drafting-engine)
5. [Phase 4: Apply Trigger Mechanism](#phase-4-apply-trigger-mechanism)
6. [Phase 5: LinkedIn Integration (Future)](#phase-5-linkedin-integration-future)
7. [Architecture Decisions](#architecture-decisions)
8. [Application Tracking System](#application-tracking-system)
9. [Efficiency Improvements](#efficiency-improvements-to-current-system)
10. [Portal Access Requirements](#portal-access-requirements)
11. [Open Questions](#open-questions)
12. [Build Order & Dependencies](#build-order--dependencies)

---

## Decisions Log

| Question | Decision | Date |
|----------|----------|------|
| Show contracts with passed deadlines? | Yes — show all, don't hide based on lead time | 20 Apr |
| Anthropic API key | Available — not a blocker | 20 Apr |
| Fit score granularity | 1-5 numeric scale | 20 Apr |
| Draft recipients (production) | All three recipients | 20 Apr |
| Draft recipients (testing) | Marta only | 20 Apr |
| Focus portal | Contracts Finder first, then expand | 20 Apr |

---

## Overview

### What exists today

- Two scrapers (Contracts Finder API + Find a Tender HTML scraping)
- QC agents filtering false positives (keyword relevance, summary quality, completeness, duplicates)
- Weekly HTML email sent every Friday via MS Graph with summary metrics
- Streamlit dashboard for live searching
- GitHub Actions for scheduling

### What's being added

| Feature | Effort | Value |
|---------|--------|-------|
| Filter expired deadlines + expiring-soon count | Small | Immediate email quality fix |
| Compatibility/fit scoring | Medium | Better decision-making per contract |
| Application drafting engine | Large | Core capability — saves hours per bid |
| Apply trigger (reply/click to draft) | Medium | Connects email to drafting pipeline |
| LinkedIn connections (future) | Large | Relationship intelligence |

---

## Phase 1: Email Quality Improvements

### 1a. Filter out expired deadlines

**Problem:** The email currently shows contracts with closing dates in the past. The email preview has contracts closing in August 2025 and January 2026 still appearing. The scrapers request "open only" but stale results slip through — especially from Find a Tender where status filtering is less reliable.

**Fix:** Add a date check in `fetch_and_filter()` that drops any contract where `closing_date < today`. All contracts with future deadlines are shown regardless of how soon they close (no minimum lead time filter — the team wants to see everything that's still open).

**Where:** `src/notifications/weekly_report.py` — after dedup, before QC filtering.

### 1b. "Expiring soon" metric in summary bar

**Problem:** The email shows total count, CF count, FaT count, and average value — but doesn't surface urgency. You have to scan the table to find what's closing soon.

**Fix:** Add a new metric card: **"Closing within 2 weeks"** showing the count of contracts with `closing_date` within 14 days. Consider also adding **"Closing this week"** if any exist, highlighted in red/orange.

**Where:** `build_email_html()` in `weekly_report.py` — add to the metrics section.

### 1c. Number each contract

**Problem:** Needed for the reply-to-apply feature (Phase 4) — users need a way to reference specific contracts.

**Fix:** Add `#1`, `#2`, etc. as a visible column or prefix on each row in the email table.

**Where:** `build_email_html()` — add number to each row.

---

## Phase 2: Compatibility Scoring

### Goal

For each contract in the email, show a fit indicator (e.g. "Strong fit", "Moderate fit", "Weak fit") so the team can quickly prioritise without reading every description.

### How it works

**Scoring dimensions:**
1. **Sector alignment** — Does the contract's domain match Inference Group's experience? (AI/data consulting for public sector = strong; physical infrastructure = weak)
2. **Capability match** — Do the required skills (NLP, data engineering, ML, etc.) map to what the team delivers?
3. **Size/scale fit** — Is the contract value in a range the company typically bids for? Is the team size realistic?
4. **Experience signals** — Has the company done similar work before? (case studies, past bids)

**Implementation — two options:**

#### Option A: Rule-based scoring (recommended for v1)

Define a scoring matrix in config — weighted keywords for strong/moderate/weak fit mapped to Inference Group's capabilities. Fast, no API cost, deterministic, easy to tune.

```
Example config:
  strong_fit_signals: ["AI strategy", "data platform", "ML pipeline", "NLP", "GenAI", "discovery phase", "consulting"]
  moderate_fit_signals: ["data analytics", "dashboard", "reporting", "digital transformation"]
  weak_fit_signals: ["hardware", "infrastructure", "networking", "construction", "facilities"]
  ideal_value_range: [25000, 250000]
```

Score = weighted sum of signal matches + value range fit + deadline feasibility.

**Pros:** No API dependency, fast, free, predictable.
**Cons:** Can't understand nuance — a "data platform for agricultural monitoring" and "data platform for NHS" would score the same.

#### Option B: Claude-powered scoring

Send each contract's title + description + company profile to Claude API and ask for a structured fit assessment (score + 1-line rationale).

**Pros:** Understands context, can explain *why* something is or isn't a fit, handles edge cases.
**Cons:** Adds API cost (~$0.01-0.03 per contract per week, so ~$0.10-0.20 per email), adds latency, needs Anthropic API key as a secret.

#### Recommendation

**Start with Option A (rule-based), add Option B later once the company profile is solid.** The rule-based approach gets 80% of the value immediately. Once the Reusable Bid Information is audited and the company profile is confirmed with Richard, Option B becomes more valuable because Claude can assess nuanced fit.

**Display in email:** 1-5 numeric score with colour-coded badge per contract:
- 5: Dark green — excellent fit
- 4: Green — strong fit
- 3: Amber — moderate fit
- 2: Light grey — weak fit
- 1: Grey — poor fit

Include a 1-line rationale beneath the score (e.g. "Strong NLP + public sector match, ideal value range") so the team understands the rating at a glance.

---

## Phase 3: Application Drafting Engine

This is the biggest and most valuable new feature. When someone selects a contract to apply for, the system should draft the application.

### The challenge

UK government tenders don't have a standard format. The application questions, submission method, and required documents vary by:
- **Portal** (each buyer uses a different e-procurement system)
- **Framework** (some are call-offs from existing frameworks like G-Cloud, DOS)
- **Procurement type** (open procedure, restricted, competitive dialogue)

### Proposed approach — layered, starting simple

#### Layer 1: Scrape the full notice (Contracts Finder focus)

When a contract is selected, fetch the **full notice page** from Contracts Finder (not just the search result). The full notice contains:
- Detailed description and scope
- Award criteria and weightings
- Document links (ITT, specification, T&Cs)
- Contact details
- Procurement timeline

**Implementation:** New function `fetch_full_notice(notice_id)` in `contracts_finder.py` that GETs the notice detail API endpoint.

The CF API has a detail endpoint: `GET /api/rest/2/search_notices/json/{notice_id}` — this returns the full notice with all fields including attached document links.

#### Layer 2: Extract questions and requirements

Parse the full notice to identify:
- **Selection/exclusion criteria** (SPD — Standard Procurement Document)
- **Award criteria** (quality/price split, evaluation weightings)
- **Technical questions** (usually free-text responses about experience, methodology, team, etc.)
- **Required documents** (case studies, CVs, policies, certificates)

For Contracts Finder, much of this is in the description text or in linked documents. We can extract what's in the API response; linked documents (PDFs, DOCXs) would need portal access.

#### Layer 3: Draft responses using Claude

For each identified question/criterion, use Claude to draft a response drawing on:
- **Company profile** — who Inference Group is, team, certifications
- **Reusable Bid Information** — pre-written answers to common questions (Notion)
- **Case studies** — past relevant work
- **Contract-specific context** — the scope, requirements, buyer sector

**Prompt structure:**
```
You are drafting a tender response for Inference Group (TCN Capital), 
a UK-based AI and data consultancy.

CONTRACT: {title} — {buyer}
SCOPE: {full description}
QUESTION: {extracted question}
EVALUATION: {criteria and weighting if available}

COMPANY CONTEXT:
{reusable bid info relevant to this question}

Draft a response that:
- Directly addresses the question
- Demonstrates relevant experience
- Is specific to this contract's scope (not generic)
- Follows UK public sector tender conventions (clear, evidence-based, numbered)
- Stays within {word limit} words if specified
```

#### Layer 4: Output format

**Option A: Email back** — Send the drafted responses as a structured HTML email. Each question/section clearly labelled, with the draft answer and a note on confidence level. The recipient can then copy-paste into the portal.

**Option B: Word document** — Generate a `.docx` with the responses formatted and ready to upload. Some portals accept document uploads.

**Option C: Structured JSON** — Output the responses in a machine-readable format that could later feed into portal auto-fill (future).

**Recommendation for v1: Option A (email).** It's consistent with the existing delivery mechanism, requires no new tools, and the recipient can copy content into whatever portal they're using. Add a clear "DRAFT — review before submitting" header.

#### What's realistic without portal access

Without logging into the e-procurement portals, we can:
- Get the full notice text from CF API (detailed description, criteria, timeline)
- Identify the general structure of what's being asked
- Draft responses to the commonly asked questions (experience, methodology, team, approach)
- Prepare standard documents (policies, case studies, CVs) that are almost always required

What we **can't** do without portal access:
- See the specific ITT (Invitation to Tender) questions and word limits
- See the evaluation scoring matrix
- Download specification documents
- Fill in the actual submission form

**This is why portal access matters** — getting access to even one portal (the most common one used by your target buyers) dramatically improves draft quality.

### How much of an application can we realistically auto-draft?

Based on typical UK gov AI/data tenders:

| Section | Auto-draftable? | Notes |
|---------|-----------------|-------|
| Company overview | Yes — reusable | Same across most bids |
| Relevant experience / case studies | Yes — reusable | Select most relevant from library |
| Methodology / approach | Partially | Generic structure + contract-specific detail from Claude |
| Team / CVs | Yes — reusable | Template CVs, select relevant people |
| Social value | Yes — reusable | Standard response, light customisation |
| Technical approach | Partially | Depends on specificity of the brief |
| Pricing | No | Needs human judgement |
| Policies (GDPR, security, insurance) | Yes — reusable | Standard documents |
| Specific ITT questions | Partially | Can draft if we have the questions; otherwise generic |

**Estimated coverage: 50-70% of a typical application can be auto-drafted or pre-populated,** rising to 80%+ once portal access provides the specific questions.

---

## Phase 4: Apply Trigger Mechanism

How does someone say "I want to apply for contract #4" and kick off the drafting?

### Options evaluated

#### Option A: Email reply monitoring

**How:** Use MS Graph API to poll the inbox for replies to the weekly report email. Parse the reply body for "apply to #4" (or similar). Trigger the drafting pipeline.

**Architecture:**
- New GitHub Actions workflow on a cron (e.g. every 30 mins during business hours)
- Calls MS Graph `GET /me/messages` filtered to replies to the weekly report
- Parses the contract number(s)
- Runs the drafting engine
- Sends the draft back via email

**Pros:** Most natural UX — just reply to the email. No extra tools needed.
**Cons:** Requires additional MS Graph permissions (`Mail.Read`), polling adds complexity, parsing free-text replies is fragile (what if someone writes "let's go for number 4 and maybe 7"?).

#### Option B: Clickable "Apply" buttons in the email

**How:** Each contract row in the email gets a small "Draft application" link/button. The link hits a lightweight endpoint that triggers the drafting workflow.

**Architecture:**
- Add a webhook endpoint (options below)
- Each "Apply" link encodes the contract ID: `https://endpoint.example.com/apply?id=abc123&email=marta@inferencegroup.com`
- Endpoint triggers GitHub Actions `workflow_dispatch` via GitHub API
- Drafting workflow runs, emails results back

**Webhook hosting options:**
1. **Cloudflare Worker** (free tier) — simple URL endpoint, triggers GH Actions
2. **GitHub Actions `repository_dispatch`** — needs a thin proxy since email links can't POST
3. **Streamlit page** — add an `/apply` route to the existing dashboard

**Pros:** One-click, no parsing ambiguity, each contract gets its own link.
**Cons:** Links in emails sometimes get flagged by spam filters. Needs a hosted endpoint.

#### Option C: Dashboard "Apply" button

**How:** Add an "Apply" button next to each contract in the Streamlit dashboard. Clicking it triggers the drafting pipeline and shows progress in the UI.

**Pros:** Rich UI, can show drafting progress, preview before sending.
**Cons:** Requires switching from email to browser. Extra step vs. just replying.

#### Option D: Combined approach (recommended)

**How:** Use email reply monitoring as the primary trigger (natural UX), with dashboard buttons as a secondary/fallback.

Simplify the parsing problem by being prescriptive about the reply format. Include instructions at the bottom of the email:

> **Want to apply?** Reply to this email with the contract numbers, e.g.:
> `Apply: #3, #5`

Parse with a simple regex: `apply.*?#(\d+)`. If parsing fails, reply asking for clarification.

**Phase the build:**
1. First: Dashboard button (simpler to build, good for testing)
2. Then: Email reply trigger (better UX once proven)

**Recommendation: Start with Option B (clickable links) + Option C (dashboard button).** Email reply monitoring is the nicest UX but hardest to get right. Clickable links in the email are nearly as convenient and much simpler. Build the dashboard button for testing, then add email links once the drafting engine works.

---

## Phase 5: LinkedIn Integration (Future)

**Goal:** For each contract in the email, check if anyone at Inference Group has a LinkedIn connection at the buying organisation.

**Why it matters:** In UK public sector procurement, you can't influence the evaluation, but you *can* get better intel during the pre-market phase, understand the buyer's real needs, and get early notice of upcoming opportunities.

**Options:**
1. **LinkedIn API (Sales Navigator)** — most complete, requires Sales Navigator subscription + API access
2. **LinkedIn basic API** — limited, doesn't expose connection graphs
3. **Manual export** — each team member exports their connections CSV, match against buyer orgs
4. **Third-party tools** — e.g. Apollo, Lusha, or similar for org-level relationship mapping

**Parking this for now.** When ready, the simplest first step is option 3: export connections, build a lookup table of organisations, match against the `buyer` field in contract results.

---

## Architecture Decisions

### Where does the drafting run?

**Option 1: GitHub Actions (recommended for v1)**

Same infrastructure as the weekly email. Add a new workflow `draft-application.yml` triggered by `workflow_dispatch` with inputs: `contract_id`, `source`, `reply_to_email`.

**Pros:** No new infrastructure, free (within GH Actions minutes), secrets already configured.
**Cons:** Not interactive — user can't see progress. Limited to 6 hours per run.

**Option 2: Streamlit app**

Add a drafting page to the existing dashboard. User selects a contract, clicks "Draft", watches progress.

**Pros:** Interactive, visual, immediate feedback.
**Cons:** Needs Anthropic API key configured in Streamlit Cloud, session-dependent (if they close the tab, drafting stops).

**Option 3: Dedicated server / cloud function**

AWS Lambda, Cloudflare Worker, or small VPS running the drafting pipeline.

**Pros:** Always available, can handle async work, scalable.
**Cons:** New infrastructure to maintain, additional cost.

**Recommendation:** Start with GitHub Actions (keeps everything in one place), add Streamlit UI later for preview/editing.

### Claude API integration

The drafting engine needs Claude. This means:
- Adding `anthropic` to `requirements.txt`
- Storing `ANTHROPIC_API_KEY` as a GitHub Actions secret
- Building prompts that use the company profile + contract context

**Cost estimate:** ~$0.05-0.15 per contract application (depending on length of tender docs and number of questions). At a few applications per week, this is negligible.

### Data persistence

Currently there's no persistent storage — results are fetched fresh each run. For the drafting pipeline, we need to:
- Store the weekly email results so the "apply to #4" reference works days later
- Store drafted applications for review and iteration

**Options:**
1. **JSON file in repo** — write `data/processed/weekly_results_{date}.json` as part of the email workflow. The drafting workflow reads it.
2. **GitHub Actions artifacts** — store results as workflow artifacts, retrieve in the drafting workflow.
3. **Notion database** — store results + drafts in Notion (aligns with existing Notion usage).

**Recommendation:** Option 1 for v1 — write a JSON file, commit it to the repo. Simple, versioned, no new dependencies.

---

## Portal Access Requirements

Contracts Finder links to external e-procurement portals where the actual submission happens. The most common portals used by UK government buyers for AI/data contracts are:

| Portal | Used by | URL |
|--------|---------|-----|
| **Jaggaer** (formerly BravoSolution) | NHS, central government, large councils | jaggaer.com |
| **In-Tend** | Local councils, NHS trusts | in-tend.co.uk |
| **Delta eSourcing** | Local authorities, housing, education | delta-esourcing.com |
| **ProContract (Due North)** | Councils, police, fire services | procontract.due-north.com |
| **Atamis** | NHS, some councils | atamis.co.uk |
| **myTenders** | Scottish public sector | myTenders.co.uk |
| **eTendersNI** | Northern Ireland public sector | etendersni.gov.uk |
| **Sell2Wales** | Welsh public sector | sell2wales.gov.wales |

**What to ask for:** Rather than registering on all of them, register on the ones your target contracts actually use. Look at the last few weekly emails — the "link" field for Contracts Finder contracts will redirect to one of these portals.

**Recommended first step:** Register on **Jaggaer** and **In-Tend** — these cover the largest share of NHS and council tenders in the AI/data space.

Registration is typically free for suppliers. You'll need:
- Company name, address, registration number
- Contact details
- Basic capability statement
- Sometimes: DUNS number, financial information

---

## Open Questions

1. **Company profile for scoring** — Is the Notion Reusable Bid Information current enough to use? If not, can Richard provide a summary of: capabilities, sectors, past contracts, team size, certifications? (Marta to check)

2. **Past successful bids** — Are there example bid responses we can use to calibrate Claude's tone and depth? (Marta to ask)

3. **Portal access** — Can someone register on Jaggaer and In-Tend so we can test fetching full ITT documents? (Marta to ask)

4. **Anthropic API key** — Do we have one, or do we need to set one up? Needed for Phase 2 Option B and all of Phase 3.

5. **Minimum deadline threshold** — Should we filter out contracts closing within, say, 3 days? (Probably not enough time to draft and submit.) What's the minimum lead time worth showing?

6. **Budget for API costs** — Claude API for drafting is cheap (~$0.10-0.15 per application), but should be explicitly approved.

---

## Build Order & Dependencies

```
Phase 1 ─── Email quality (no dependencies, build first)
  │
  ├── 1a. Filter expired deadlines
  ├── 1b. Expiring-soon metric
  └── 1c. Number contracts in email
  │
Phase 2 ─── Compatibility scoring (depends on company profile input)
  │         Start rule-based (no deps), upgrade to Claude later
  │
Phase 3 ─── Application drafting engine (biggest piece)
  │
  ├── 3a. Persist weekly results to JSON (needed by apply trigger)
  ├── 3b. Full notice fetcher for CF (detail API endpoint)
  ├── 3c. Question/requirement extractor
  ├── 3d. Claude drafting prompts + company context
  ├── 3e. Draft output as email
  │         Depends on: Anthropic API key, company profile, ideally past bids
  │
Phase 4 ─── Apply trigger mechanism
  │
  ├── 4a. Dashboard "Apply" button (for testing)
  ├── 4b. Clickable "Draft application" links in email
  │         Depends on: Phase 3 working, webhook endpoint
  │
Phase 5 ─── LinkedIn (future, no current dependencies)
```

### Suggested timeline

- **Phase 1:** Can be built immediately — no blockers
- **Phase 2 (rule-based):** Can be built immediately — company profile improves it but isn't blocking
- **Phase 3:** Blocked on Anthropic API key. Improved by portal access + past bids, but can start with CF API data only
- **Phase 4:** Blocked on Phase 3 being functional

---

## Application Tracking System

### Why

Currently every weekly email is a fresh, stateless snapshot. There's no way to know:
- Which contracts the team has already seen
- Which ones someone is actively applying for
- Which ones have been submitted, won, or lost
- Whether a contract appeared last week too (is it new or recurring?)

Tracking solves this and also powers the "new this week" label and prevents duplicate drafting work.

### Contract lifecycle

```
DISCOVERED  →  REVIEWED  →  SHORTLISTED  →  DRAFTING  →  SUBMITTED  →  OUTCOME
   (auto)       (human)      (human)        (auto+human)  (human)      (human)
                                                                     ┌─ WON
                                                                     ├─ LOST
                                                                     └─ WITHDRAWN
```

Each contract gets a status from this set:
- **new** — first seen this week (auto-set on first scrape)
- **seen** — appeared in a previous email but no action taken
- **shortlisted** — someone flagged interest but not yet applying
- **drafting** — application draft in progress
- **draft_sent** — draft emailed to team for review
- **submitted** — application submitted to the portal
- **won** — contract awarded to Inference Group
- **lost** — contract awarded to someone else or withdrawn
- **skipped** — explicitly marked as not pursuing

### Storage: `data/tracking/contracts.json`

A single JSON file, committed to the repo, that accumulates over time. Each entry is keyed by `ocid` (unique contract identifier from the portals).

```json
{
  "ocid-abc123": {
    "ocid": "ocid-abc123",
    "title": "AI Discovery for Early Conciliation",
    "buyer": "UK SBS",
    "source": "Contracts Finder",
    "closing_date": "2026-06-15",
    "total_value": 100000,
    "link": "https://...",
    "fit_score": 4,
    "status": "drafting",
    "first_seen": "2026-04-18",
    "last_seen": "2026-04-25",
    "status_history": [
      {"status": "new", "date": "2026-04-18", "by": "system"},
      {"status": "shortlisted", "date": "2026-04-19", "by": "richard@inferencegroup.com"},
      {"status": "drafting", "date": "2026-04-20", "by": "marta@inferencegroup.com"}
    ],
    "draft_id": "draft_2026-04-20_abc123",
    "notes": ""
  }
}
```

### How it integrates with each phase

**Weekly email (Phase 1):**
- After fetching results, merge with `contracts.json`
- New contracts get status `new`; existing ones get `last_seen` updated
- Contracts already in `drafting`/`submitted`/`won` status are flagged in the email with a badge (e.g. "Applying" tag in orange, "Submitted" tag in green)
- Expired contracts (closing_date < today) with status `new`/`seen` get auto-set to `skipped` with reason "deadline passed"

**Apply trigger (Phase 4):**
- When someone clicks "Draft application" for contract #4, the tracking file updates status to `drafting`
- When draft is emailed, status updates to `draft_sent`
- Prevents duplicate drafting — if status is already `drafting` or later, warn instead of re-running

**Dashboard:**
- Add a "Pipeline" tab showing all tracked contracts grouped by status
- Filter/sort by status, fit score, deadline
- Allow manual status updates (e.g. mark as submitted, won, lost)

### How tracking gets updated

| Action | Trigger | Who |
|--------|---------|-----|
| New contract discovered | Weekly scrape | System (auto) |
| Mark as shortlisted | Dashboard button or email reply | Human |
| Start drafting | Apply trigger (email link / dashboard) | Human triggers, system updates |
| Draft complete | Drafting engine finishes | System (auto) |
| Mark as submitted | Dashboard button or email reply | Human |
| Mark as won/lost | Dashboard button | Human |
| Auto-expire | Weekly scrape finds closing_date < today | System (auto) |

### Email integration: "In Progress" section

Add a new section to the weekly email, above the main table:

```
┌──────────────────────────────────────────────────────────┐
│  YOUR PIPELINE                                           │
│                                                          │
│  Drafting (2)                                            │
│   • #3 AI Discovery — UK SBS — closes 15 Jun — draft    │
│     sent 20 Apr                                          │
│   • #7 Data Platform — NHS — closes 22 Jun — drafting   │
│                                                          │
│  Submitted (1)                                           │
│   • SOL30245 AI Delivery — Solihull — awaiting outcome   │
│                                                          │
│  Won (1)                                                 │
│   • GenAI Pilot — Redcar — awarded 12 Apr               │
└──────────────────────────────────────────────────────────┘
```

This gives instant visibility on active bids without leaving the email.

### Git workflow for tracking updates

The tracking file lives in `data/tracking/contracts.json`. Updates happen via:
1. **Weekly email workflow** — commits updated tracking after each run
2. **Apply workflow** — commits status change when drafting starts/finishes
3. **Dashboard** — for manual updates, commits via a small API endpoint or the user runs a script

GitHub Actions needs write permission to the repo. The default `GITHUB_TOKEN` can do this with `contents: write` in the workflow.

---

## Efficiency Improvements to Current System

### 1. Scraper date range is too wide

**Problem:** Both scrapers search from `2020-01-01` to today. This pulls thousands of historical (mostly closed) results that get fetched, transferred, and then discarded. Wastes time, bandwidth, and API quota.

**Solution:** Use `published_days_back` from `search_criteria.json` (currently set to 30). Change `fetch_and_filter()` to calculate `date_from = today - 30 days` instead of hardcoding 2020.

**But:** With the new tracking system, we want to also re-check contracts we've seen before (they might have updated status/dates). So the approach is:

1. **Primary search:** Last 30 days (catches new contracts)
2. **Tracked contract refresh:** For any contract in `contracts.json` with status `new`/`seen`/`shortlisted`/`drafting`, re-fetch its current data to check if it's still open, deadline changed, etc. This is a targeted lookup by ID, not a broad search.

**Where:** `src/notifications/weekly_report.py` — change `date_from` in `fetch_and_filter()`. Add a `refresh_tracked()` function.

**Impact:** Reduces CF API results from potentially thousands to dozens. FaT pages from 5 to 1-2. Run time drops from ~30s to ~10s.

### 2. Persistent storage between runs

**Problem:** Each weekly run is stateless — no memory of what was sent before.

**Solution:** The tracking system (see above) solves this. `data/tracking/contracts.json` persists across runs. Additionally:

- **Weekly results snapshot:** Write `data/processed/weekly_{date}.json` after each email run. This is the exact list that was emailed, with contract numbers (#1-#N). The apply trigger reads this file to resolve "apply to #4".
- **Retention:** Keep the last 8 weekly snapshots (2 months). Older ones get auto-deleted by the workflow.
- **"New this week" badge:** Compare this week's results against `contracts.json` — any contract not previously seen gets a "NEW" tag in the email.

**Where:**
- New file: `src/tracking/store.py` — functions to read/write/update tracking
- Modified: `weekly_report.py` — write snapshot + update tracking after each run
- Modified: `build_email_html()` — add "NEW" badge for first-seen contracts

### 3. Find a Tender scraping is fragile

**Problem:** FaT uses HTML scraping via BeautifulSoup. If they change their page structure (class names, layout), the scraper silently returns 0 results or garbage.

**Solution — three layers of protection:**

**Layer 1: Result count sanity check**
After scraping, compare result count to recent history. If FaT returns 0 results when it usually returns 5-15, something is wrong. Log a warning and flag it in the email.

```python
# In fetch_and_filter(), after FaT scrape:
if len(fat_results) == 0:
    warnings.append("Find a Tender returned 0 results — possible scraping issue")
```

**Layer 2: Structure validation**
After parsing each result, check that critical fields (title, buyer, link) are non-empty. If >50% of results have empty titles, the HTML structure likely changed.

**Layer 3: Alert on failure**
If the FaT scraper raises an exception or returns suspicious results, send a separate alert email to Marta:

```
Subject: [Alert] Find a Tender scraper may be broken
Body: FaT returned 0 results this week. This usually means the page structure changed.
      Check src/scrapers/find_a_tender.py and test manually.
      This week's email was sent with Contracts Finder results only.
```

**Where:**
- Modified: `src/scrapers/find_a_tender.py` — add validation in `fetch_notices()`
- Modified: `weekly_report.py` — add sanity check + alert logic
- New section in email: optional warning banner if a source had issues

### 4. QC agents not surfaced in email

**Problem:** Only `audit_keyword_relevance` runs for the email (to filter false positives). The other three agents (summary quality, completeness, duplicates) only show in the Streamlit dashboard.

**Solution:** Run the full QC suite during the email build and surface a summary:

**In the email metrics bar, add:**
- "X possible duplicates" (if any duplicate groups found)
- "X incomplete records" (if any have completeness score < 7)

**In the table, per-row:**
- If a contract is part of a duplicate group, show a small "Dup?" indicator
- If a contract has low completeness, show a warning icon next to missing fields (e.g. "Value: TBC" already exists, but could add "Missing: location, CPV")

**Keep it lightweight** — the email shouldn't become a QC dashboard. Just surface the most actionable flags.

**Where:** Modified `weekly_report.py` — call `run_all_qc()` instead of just `audit_keyword_relevance()`, use findings to annotate the HTML.

### 5. Email sending has no retry or error notification

**Problem:** If MS Graph returns an error (expired token, rate limit, outage), the GitHub Actions workflow fails silently. Nobody knows the email wasn't sent until someone notices on Friday afternoon.

**Solution:**

**Retry logic:**
```python
def send_email_with_retry(html_body, recipients, subject, from_email, max_retries=3):
    for attempt in range(max_retries):
        try:
            send_email(html_body, recipients, subject, from_email)
            return  # success
        except RuntimeError as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt+1} failed: {e}. Retrying in 30s...")
                time.sleep(30)
            else:
                raise
```

**Failure notification:**
If all retries fail, the GitHub Actions workflow should still notify someone. Options:

1. **GitHub Actions built-in:** The workflow already shows as failed in the Actions tab. Add an email notification step that runs `if: failure()`:
   ```yaml
   - name: Alert on failure
     if: failure()
     run: |
       # Use a simple curl to MS Graph or a fallback SMTP to send alert
       echo "Weekly report failed — check GitHub Actions"
   ```

2. **Better:** Add the `anthropic` API key now and use Claude to compose a diagnostic message if the failure is ambiguous.

**Token expiry pre-check:**
Before sending, test the token with a lightweight Graph call (`GET /me`). If it fails, the error message can specifically say "refresh token expired — run `python scripts/get_ms_token.py`".

**Where:**
- Modified: `weekly_report.py` — add retry wrapper around `send_email()`
- Modified: `.github/workflows/weekly-contracts.yml` — add failure notification step

### 6. Summary extraction could use Claude

**Problem:** `_summarise()` uses regex heuristics to find a "scope sentence" from the description. It often picks awkward fragments (e.g. "Award of contract for Generative AI Summary Development Pilot" — that's a process statement, not a scope description).

**Solution:** Replace the regex approach with a Claude API call once we have the key configured:

```python
def _summarise_with_claude(r: dict) -> str:
    """Use Claude to generate a one-line scope summary."""
    prompt = f"""Summarise this government contract in one sentence (max 20 words).
Focus on what is being delivered, not the procurement process.

Title: {r['title']}
Description: {r['description'][:500]}

One-sentence summary:"""
    
    # Call Claude API (Haiku for speed + cost)
    response = anthropic.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=60,
        messages=[{"role": "user", "content": prompt}]
    )
    return response.content[0].text.strip()
```

**Cost:** ~$0.001 per summary. At 10-20 contracts per email = ~$0.02 per week. Negligible.

**Fallback:** Keep the current regex `_summarise()` as fallback if the API call fails or the key isn't configured. This way the email still works without an API key.

**Where:** Modified `weekly_report.py` — add `_summarise_with_claude()`, use it with fallback to `_summarise()`.

**Model choice:** Use Haiku (fast, cheap) for summaries. Save Sonnet/Opus for the drafting engine where quality matters more.

---

## Portal Access Requirements

Contracts Finder links to external e-procurement portals where the actual submission happens. The most common portals used by UK government buyers for AI/data contracts are:

| Portal | Used by | URL |
|--------|---------|-----|
| **Jaggaer** (formerly BravoSolution) | NHS, central government, large councils | jaggaer.com |
| **In-Tend** | Local councils, NHS trusts | in-tend.co.uk |
| **Delta eSourcing** | Local authorities, housing, education | delta-esourcing.com |
| **ProContract (Due North)** | Councils, police, fire services | procontract.due-north.com |
| **Atamis** | NHS, some councils | atamis.co.uk |
| **myTenders** | Scottish public sector | myTenders.co.uk |
| **eTendersNI** | Northern Ireland public sector | etendersni.gov.uk |
| **Sell2Wales** | Welsh public sector | sell2wales.gov.wales |

**What to ask for:** Rather than registering on all of them, register on the ones your target contracts actually use. Look at the last few weekly emails — the "link" field for Contracts Finder contracts will redirect to one of these portals.

**Recommended first step:** Register on **Jaggaer** and **In-Tend** — these cover the largest share of NHS and council tenders in the AI/data space.

Registration is typically free for suppliers. You'll need:
- Company name, address, registration number
- Contact details
- Basic capability statement
- Sometimes: DUNS number, financial information

---

## Open Questions

### Resolved

| # | Question | Answer |
|---|----------|--------|
| 1 | Anthropic API key | Available — add as `ANTHROPIC_API_KEY` GitHub secret |
| 2 | Minimum deadline threshold | Show all — no minimum lead time filter |
| 3 | Fit score granularity | 1-5 numeric scale with colour coding |
| 4 | Draft recipients (production) | All three recipients |
| 5 | Draft recipients (testing) | Marta only |
| 6 | Portal focus | Contracts Finder first |

### Still open

1. **Company profile for scoring** — Is the Notion Reusable Bid Information current enough to use? If not, can Richard provide a summary of: capabilities, sectors, past contracts, team size, certifications? (Marta to check)

2. **Past successful bids** — Are there example bid responses we can use to calibrate Claude's tone and depth? (Marta to ask)

3. **Portal access** — Can someone register on Jaggaer and In-Tend so we can test fetching full ITT documents? (Marta to ask)

4. **Budget for API costs** — Claude API for summaries (~$0.02/week) and drafting (~$0.10-0.15 per application) is cheap, but should be explicitly approved.

5. **Tracking workflow** — Who should be able to update contract statuses (submitted, won, lost)? All three recipients, or just Marta? This affects whether we need auth on the dashboard status updates.

6. **Git write access from Actions** — The tracking system needs the weekly workflow to commit `contracts.json` back to the repo. This requires `contents: write` permission on the `GITHUB_TOKEN`. Is this acceptable, or do we need a different persistence approach?

---

## Build Order & Dependencies

```
Phase 0 ─── Efficiency & infrastructure (no blockers, build alongside Phase 1)
  │
  ├── 0a. Narrow scraper date range (30 days instead of 2020)
  ├── 0b. FaT scraper health checks + failure alerts
  ├── 0c. Email send retry + failure notification
  ├── 0d. Tracking system: data/tracking/contracts.json + store.py
  ├── 0e. Weekly results snapshot: data/processed/weekly_{date}.json
  │
Phase 1 ─── Email quality (no blockers)
  │
  ├── 1a. Filter expired deadlines (closing_date < today)
  ├── 1b. Expiring-soon metric card
  ├── 1c. Number contracts (#1, #2, ...)
  ├── 1d. "NEW" badge for first-seen contracts (needs 0d)
  ├── 1e. "In Progress" pipeline section in email (needs 0d)
  ├── 1f. Surface QC flags in email (duplicates, incomplete)
  │
Phase 2 ─── Compatibility scoring
  │
  ├── 2a. Rule-based scoring (config-driven, no API needed)
  ├── 2b. Score column + colour badge in email
  ├── 2c. Upgrade to Claude-powered scoring (needs API key as secret)
  │         Improved by: company profile from Richard
  │
Phase 3 ─── Application drafting engine
  │
  ├── 3a. Claude-powered summaries (replace regex _summarise) — can do early
  ├── 3b. Full notice fetcher for CF (detail API endpoint)
  ├── 3c. Question/requirement extractor
  ├── 3d. Claude drafting prompts + company context
  ├── 3e. Draft output as email to all recipients
  ├── 3f. Update tracking status on draft start/complete
  │         Depends on: API key as secret, company profile, ideally past bids
  │
Phase 4 ─── Apply trigger mechanism
  │
  ├── 4a. Dashboard "Apply" button (for testing, sends draft to Marta)
  ���── 4b. Clickable "Draft application" links in email
  ├── 4c. Dashboard "Pipeline" tab (view/update tracking statuses)
  │         Depends on: Phase 3 working, webhook endpoint, tracking system
  │
Phase 5 ─── LinkedIn (future, no current dependencies)
```

### What can be built right now (no blockers)

| Item | Description |
|------|-------------|
| 0a | Narrow scraper date range to 30 days |
| 0b | FaT health checks + alert |
| 0c | Email retry + failure notification |
| 0d | Tracking system (`contracts.json` + `store.py`) |
| 0e | Weekly results snapshot |
| 1a | Filter expired deadlines |
| 1b | Expiring-soon metric |
| 1c | Number contracts |
| 2a | Rule-based fit scoring |

### What needs the API key added as a GitHub secret first

| Item | Description |
|------|-------------|
| 2c | Claude-powered fit scoring |
| 3a | Claude-powered summaries |
| 3b-3f | Full drafting engine |

### What needs human input first

| Item | Blocker | Who |
|------|---------|-----|
| 2c (improved) | Company profile / capabilities summary | Richard |
| 3d (improved) | Past successful bid examples | Marta to ask |
| 3b (improved) | Portal registration (Jaggaer, In-Tend) | Marta to ask |

---

*This document will be updated as decisions are made on the open questions.*
