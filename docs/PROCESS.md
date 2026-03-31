# Process & Workflow

## Pipeline Overview

```
1. SCRAPE       →  2. STORE       →  3. FILTER      →  4. SHORTLIST   →  5. DRAFT       →  6. SUBMIT
   (scheduled)     (centralised)     (Claude)          (human review)    (Claude + KB)     (human review)
```

## Stage Details

### 1. Scrape — Tender Discovery
- **Sources:** Contracts Finder, Find a Tender
- **Frequency:** Scheduled (e.g. every Friday morning)
- **Method:** Automated scraping of search results using criteria from previous Notion filters
- **Output:** Raw opportunity data (title, description, value, deadline, sector, link)

### 2. Store — Central Repository
- **Location:** TBD — options are Notion database, SharePoint list, or local storage
- **Historical reference:** SharePoint list previously used by Anna (see LINKS.md)
- **Data captured per opportunity:** title, contracting authority, value, deadline, sector/CPV, region, status, link, relevance score

### 3. Filter — Relevance Assessment
- **Method:** Claude evaluates each opportunity against defined criteria
- **Criteria:** sector fit, contract value, eligibility, geography, deadline feasibility
- **Output:** relevance score + brief rationale for each opportunity

### 4. Shortlist — Human Review
- **Who:** Richard (+ team as needed)
- **Action:** Review filtered opportunities, mark as pursue / watch / skip
- **Trigger:** Weekly notification with shortlisted opportunities

### 5. Draft — Application Support
- **Knowledge base:** Reusable Bid Information (Notion) + case studies, policies, credentials
- **Method:** Claude drafts answers to common tender questions drawing on the knowledge base
- **Output:** Pre-filled response templates ready for human editing
- **Note:** Existing reusable bid content needs auditing — some may be out of date

### 6. Submit — Final Review & Submission
- **Who:** Human review and sign-off
- **Checklist:** Compliance requirements, document assembly, submission method
- **Tracking:** Update opportunity status in central repository
