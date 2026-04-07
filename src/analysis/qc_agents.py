"""
Quality Control Agents for Gov Contracts Dashboard
====================================================
Four QC agents that audit search results for relevance, summary quality,
data completeness, and duplicates.

Each agent takes a list of result dicts and returns a list of QC findings.
"""

import re
from difflib import SequenceMatcher


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

# Core keywords that indicate genuine AI/Data relevance
CORE_TERMS = {
    "artificial intelligence", "ai", "machine learning", "ml", "deep learning",
    "generative ai", "genai", "llm", "large language model", "nlp",
    "natural language processing", "computer vision", "data science",
    "data analytics", "data engineering", "data platform", "predictive analytics",
    "advanced analytics", "mlops", "foundation model", "chatbot",
    "virtual assistant", "neural network", "algorithm", "rag",
    "retrieval augmented generation", "agentic ai",
}

# Terms that appear in boilerplate, not genuine scope
BOILERPLATE_CONTEXTS = {
    "data protection", "gdpr", "data sharing agreement", "freedom of information",
    "data processor", "data controller", "privacy notice", "cookie",
    "data retention", "subject access request", "data breach",
}


# ---------------------------------------------------------------------------
# Agent 1: Keyword Relevance Auditor
# ---------------------------------------------------------------------------

def audit_keyword_relevance(results: list[dict]) -> list[dict]:
    """Check each result for genuine AI/data relevance.

    Returns a list of findings, one per result, with:
    - ocid, title, relevance_score (1-5), matched_terms, match_location,
      is_false_positive, reason
    """
    findings = []

    for r in results:
        title = (r.get("title") or "").lower()
        desc = (r.get("description") or "").lower()
        full_text = f"{title} {desc}"

        # Find which core terms match and WHERE
        title_matches = []
        desc_matches = []
        boilerplate_matches = []

        for term in CORE_TERMS:
            pattern = r"\b" + re.escape(term) + r"\b"
            if re.search(pattern, title):
                title_matches.append(term)
            if re.search(pattern, desc):
                # Check if the match is near boilerplate context
                for bp in BOILERPLATE_CONTEXTS:
                    if bp in desc:
                        # Check proximity — within 50 chars of the term
                        term_pos = desc.find(term)
                        bp_pos = desc.find(bp)
                        if term_pos >= 0 and bp_pos >= 0 and abs(term_pos - bp_pos) < 80:
                            boilerplate_matches.append(term)
                            break
                else:
                    desc_matches.append(term)

        all_matches = set(title_matches + desc_matches)
        boilerplate_only = set(boilerplate_matches) - all_matches

        # Score 1-5
        score = 0
        if title_matches:
            score += 3  # strong signal — term in title
        if desc_matches:
            score += min(len(desc_matches), 2)  # up to +2 for desc matches
        if boilerplate_only and not title_matches and not desc_matches:
            score = 1  # only matched in boilerplate context

        score = max(1, min(5, score))

        # Determine if false positive
        is_fp = score <= 1 and not title_matches
        reason = ""
        if is_fp:
            if boilerplate_only:
                reason = f"Keyword(s) {', '.join(boilerplate_only)} only appear near GDPR/data protection boilerplate"
            elif not all_matches:
                reason = "No core AI/data terms found in title or description"
            else:
                reason = "Low relevance — keyword match is weak or tangential"

        match_location = []
        if title_matches:
            match_location.append("title")
        if desc_matches:
            match_location.append("description")
        if boilerplate_only:
            match_location.append("boilerplate")

        findings.append({
            "ocid": r.get("ocid", ""),
            "title": r.get("title", ""),
            "source": r.get("source", ""),
            "relevance_score": score,
            "matched_terms": list(all_matches | boilerplate_only),
            "title_matches": title_matches,
            "match_location": ", ".join(match_location) if match_location else "none",
            "is_false_positive": is_fp,
            "reason": reason,
        })

    return findings


# ---------------------------------------------------------------------------
# Agent 2: Summary Quality Assessor
# ---------------------------------------------------------------------------

def audit_summary_quality(results: list[dict], summaries: list[str]) -> list[dict]:
    """Check each summary actually describes the deliverable.

    Returns findings with: quality_score (1-5), issues[], suggestion
    """
    findings = []

    for r, summary in zip(results, summaries):
        title = r.get("title", "")
        buyer = r.get("buyer", "")
        description = r.get("description", "")
        issues = []

        # Check: is it just the title repeated?
        if summary.strip().lower() == title.strip().lower():
            issues.append("Summary is identical to the title — no additional insight")

        # Check: is it just the buyer name?
        if summary.strip().lower() == buyer.strip().lower():
            issues.append("Summary is just the buyer name")

        # Check: does it describe a deliverable?
        deliverable_words = (
            "deliver", "develop", "build", "design", "implement", "deploy",
            "support", "consult", "research", "analys", "automat", "model",
            "platform", "framework", "system", "solution", "service",
            "strategy", "roadmap", "pilot", "transformation", "provide",
        )
        has_deliverable = any(w in summary.lower() for w in deliverable_words)
        if not has_deliverable:
            issues.append("Summary doesn't describe what would be delivered")

        # Check: is it boilerplate/process text?
        process_words = (
            "award notice", "call for competition", "contract has been awarded",
            "procurement is being", "please refer", "login", "register",
            "terms and conditions", "further competition",
        )
        has_process = any(w in summary.lower() for w in process_words)
        if has_process:
            issues.append("Summary contains procurement process text, not scope")

        # Check: is it too short to be useful?
        word_count = len(summary.split())
        if word_count < 5:
            issues.append(f"Summary too short ({word_count} words)")

        # Check: is it truncated awkwardly?
        if summary.endswith("...") and word_count < 8:
            issues.append("Summary is truncated too early to be meaningful")

        # Score
        score = 5
        if issues:
            score = max(1, 5 - len(issues))

        # Suggest improvement if description has content we missed
        suggestion = ""
        if issues and description and len(description) > 50:
            suggestion = "Description has content that could produce a better summary — review summarise() extraction"

        findings.append({
            "ocid": r.get("ocid", ""),
            "title": title[:60],
            "summary": summary,
            "quality_score": score,
            "word_count": word_count,
            "has_deliverable": has_deliverable,
            "has_process_text": has_process,
            "issues": issues,
            "suggestion": suggestion,
        })

    return findings


# ---------------------------------------------------------------------------
# Agent 3: Data Completeness Checker
# ---------------------------------------------------------------------------

def audit_data_completeness(results: list[dict]) -> list[dict]:
    """Check for missing or inconsistent data.

    Returns findings per result with: completeness_score, missing_fields[]
    """
    EXPECTED_FIELDS = {
        "title": "Title",
        "buyer": "Buyer",
        "total_value": "Value",
        "published_date": "Published date",
        "closing_date": "Closing date",
        "description": "Description",
        "link": "Link",
        "location": "Location",
    }

    DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}")

    findings = []

    for r in results:
        missing = []
        warnings = []

        for field, label in EXPECTED_FIELDS.items():
            val = r.get(field)
            if val is None or val == "" or val == 0:
                missing.append(label)

        # Check date format consistency
        for date_field in ("published_date", "closing_date"):
            val = r.get(date_field, "")
            if val and not DATE_PATTERN.match(str(val)):
                warnings.append(f"{date_field} not in ISO format: '{val}'")

        # Check for bogus dates
        pub = r.get("published_date", "")
        if pub and pub.startswith("0001"):
            warnings.append("Published date is placeholder (0001-)")
            missing.append("Published date (placeholder)")

        close = r.get("closing_date", "")
        if close and close.startswith("0001"):
            warnings.append("Closing date is placeholder (0001-)")
            missing.append("Closing date (placeholder)")

        # Check link is a valid URL
        link = r.get("link", "")
        if link and not link.startswith("http"):
            warnings.append(f"Link doesn't look like a URL: {link[:50]}")

        # Score: 10 = all fields present, subtract per missing
        total_fields = len(EXPECTED_FIELDS)
        present = total_fields - len([m for m in missing if "placeholder" not in m])
        score = round(present / total_fields * 10)

        findings.append({
            "ocid": r.get("ocid", ""),
            "title": r.get("title", "")[:60],
            "source": r.get("source", ""),
            "completeness_score": score,
            "missing_fields": missing,
            "warnings": warnings,
            "fields_present": f"{present}/{total_fields}",
        })

    return findings


# ---------------------------------------------------------------------------
# Agent 4: Duplicate & Overlap Detector
# ---------------------------------------------------------------------------

def audit_duplicates(results: list[dict], similarity_threshold: float = 0.75) -> list[dict]:
    """Detect probable duplicates and related notices across sources.

    Uses fuzzy title matching to find contracts that escaped ocid-based dedup.
    Returns a list of duplicate groups.
    """
    groups = []
    used = set()

    for i, r1 in enumerate(results):
        if i in used:
            continue

        t1 = r1.get("title", "").lower().strip()
        if not t1:
            continue

        group = [r1]

        for j, r2 in enumerate(results[i + 1:], start=i + 1):
            if j in used:
                continue

            t2 = r2.get("title", "").lower().strip()
            if not t2:
                continue

            similarity = SequenceMatcher(None, t1, t2).ratio()

            # Also check buyer match for extra confidence
            b1 = r1.get("buyer", "").lower()
            b2 = r2.get("buyer", "").lower()
            buyer_match = SequenceMatcher(None, b1, b2).ratio() > 0.7 if b1 and b2 else False

            if similarity >= similarity_threshold or (similarity >= 0.6 and buyer_match):
                group.append(r2)
                used.add(j)

        if len(group) > 1:
            used.add(i)

            # Classify the relationship
            sources = set(r.get("source") for r in group)
            types = set(r.get("ct") for r in group)
            relationship = "exact duplicate"
            if len(sources) > 1:
                relationship = "cross-source duplicate"
            if len(types) > 1:
                relationship = "related notices (different stages)"

            groups.append({
                "group_size": len(group),
                "relationship": relationship,
                "title_sample": group[0]["title"][:60],
                "sources": list(sources),
                "types": list(types),
                "notices": [
                    {
                        "ocid": r.get("ocid", ""),
                        "title": r.get("title", "")[:60],
                        "source": r.get("source", ""),
                        "type": r.get("ct", ""),
                        "value": r.get("total_value"),
                    }
                    for r in group
                ],
            })

    return groups


# ---------------------------------------------------------------------------
# Run all QC agents
# ---------------------------------------------------------------------------

def run_all_qc(results: list[dict], summaries: list[str]) -> dict:
    """Run all four QC agents and return a consolidated report."""
    relevance = audit_keyword_relevance(results)
    summary_quality = audit_summary_quality(results, summaries)
    completeness = audit_data_completeness(results)
    duplicates = audit_duplicates(results)

    # Aggregate stats
    total = len(results)
    false_positives = [f for f in relevance if f["is_false_positive"]]
    low_quality_summaries = [f for f in summary_quality if f["quality_score"] <= 2]
    incomplete = [f for f in completeness if f["completeness_score"] < 7]

    avg_relevance = sum(f["relevance_score"] for f in relevance) / max(total, 1)
    avg_summary = sum(f["quality_score"] for f in summary_quality) / max(total, 1)
    avg_completeness = sum(f["completeness_score"] for f in completeness) / max(total, 1)

    return {
        "summary": {
            "total_results": total,
            "avg_relevance_score": round(avg_relevance, 1),
            "avg_summary_quality": round(avg_summary, 1),
            "avg_completeness": round(avg_completeness, 1),
            "false_positives": len(false_positives),
            "low_quality_summaries": len(low_quality_summaries),
            "incomplete_records": len(incomplete),
            "duplicate_groups": len(duplicates),
        },
        "relevance": relevance,
        "summary_quality": summary_quality,
        "completeness": completeness,
        "duplicates": duplicates,
    }
