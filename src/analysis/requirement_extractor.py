"""
Requirement Extractor
======================
Parses a full contract notice to identify:
- Standard sections that need responses (methodology, experience, team, etc.)
- Evaluation criteria and weightings
- Required documents
- Key constraints (word limits, page limits, deadlines)

Works with data from contracts_finder.fetch_full_notice().
"""

import re
import html as html_mod


# Common tender question categories and the phrases that signal them
QUESTION_PATTERNS = {
    "methodology": [
        r"methodology", r"approach", r"how you will deliver",
        r"proposed solution", r"delivery approach", r"technical approach",
        r"method statement", r"work plan", r"project plan",
    ],
    "experience": [
        r"relevant experience", r"previous experience", r"track record",
        r"case stud(?:y|ies)", r"similar (?:project|contract|work)",
        r"examples of", r"evidence of delivery", r"past performance",
    ],
    "team": [
        r"proposed team", r"key personnel", r"staff(?:ing)?",
        r"resource(?:s|ing)", r"cv", r"curriculum vitae",
        r"named individuals", r"team structure", r"organis?ation chart",
    ],
    "social_value": [
        r"social value", r"community benefit", r"environmental",
        r"sustainability", r"net zero", r"carbon",
        r"apprentice", r"local (?:employment|economy|supply)",
    ],
    "quality_assurance": [
        r"quality (?:assurance|management|control)",
        r"iso\s*(?:9001|27001|14001)", r"accreditation",
        r"cyber essentials", r"security", r"data protection",
        r"gdpr compliance",
    ],
    "pricing": [
        r"pric(?:e|ing)", r"cost", r"rate card", r"day rate",
        r"schedule of rates", r"financial proposal", r"commercial",
        r"budget", r"value for money",
    ],
    "risk_management": [
        r"risk (?:management|mitigation|register)",
        r"contingency", r"business continuity",
        r"disaster recovery",
    ],
    "mobilisation": [
        r"mobilis(?:ation|e)", r"transition", r"onboarding",
        r"implementation plan", r"kick.?off", r"ramp.?up",
    ],
    "innovation": [
        r"innovation", r"added value", r"continuous improvement",
        r"lessons? learn(?:ed|t)", r"knowledge transfer",
    ],
}

# Award criteria patterns
CRITERIA_PATTERNS = [
    (r"quality[:\s]*(\d+)\s*%", "quality"),
    (r"technical[:\s]*(\d+)\s*%", "technical"),
    (r"price[:\s]*(\d+)\s*%", "price"),
    (r"cost[:\s]*(\d+)\s*%", "cost"),
    (r"social\s+value[:\s]*(\d+)\s*%", "social_value"),
    (r"(\d+)\s*%\s*(?:quality|technical)", "quality"),
    (r"(\d+)\s*%\s*(?:price|cost)", "price"),
]


def extract_requirements(notice: dict) -> dict:
    """Analyse a full notice and return structured requirements.

    Args:
        notice: Dict from contracts_finder.fetch_full_notice()

    Returns dict with:
        - sections: list of identified sections to draft
        - criteria: award criteria and weightings
        - documents: required documents
        - constraints: deadlines, word limits, etc.
        - summary: one-paragraph scope summary for context
    """
    description = notice.get("full_description") or notice.get("description", "")
    additional = notice.get("additional_text", "")
    title = notice.get("title", "")
    full_text = f"{title} {description} {additional}"

    # Clean HTML
    clean_text = re.sub(r"<[^>]+>", " ", full_text)
    clean_text = html_mod.unescape(clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    sections = _identify_sections(clean_text)
    criteria = _extract_criteria(clean_text, notice)
    documents = _extract_document_requirements(clean_text, notice)
    constraints = _extract_constraints(clean_text, notice)

    return {
        "title": title,
        "buyer": notice.get("buyer", ""),
        "value": notice.get("total_value"),
        "closing_date": notice.get("closing_date", ""),
        "procedure_type": notice.get("procedure_type", ""),
        "sections": sections,
        "criteria": criteria,
        "documents_required": documents,
        "documents_available": notice.get("documents", []),
        "constraints": constraints,
        "full_text": clean_text,
        "contact_name": notice.get("contact_name", ""),
        "contact_email": notice.get("contact_email", ""),
    }


def _identify_sections(text: str) -> list[dict]:
    """Identify which response sections are needed based on text analysis."""
    text_lower = text.lower()
    sections = []

    for section_name, patterns in QUESTION_PATTERNS.items():
        matches = []
        for pattern in patterns:
            found = list(re.finditer(pattern, text_lower))
            if found:
                matches.extend(found)

        if matches:
            # Extract context around each match (50 chars either side)
            contexts = []
            for m in matches[:3]:  # Cap at 3 excerpts
                start = max(0, m.start() - 80)
                end = min(len(text), m.end() + 80)
                excerpt = text[start:end].strip()
                contexts.append(f"...{excerpt}...")

            sections.append({
                "section": section_name,
                "confidence": min(len(matches) / 2, 1.0),  # More matches = more confident
                "match_count": len(matches),
                "context": contexts,
            })

    # Always include these standard sections even if not explicitly mentioned
    standard_sections = ["methodology", "experience", "team"]
    for ss in standard_sections:
        if not any(s["section"] == ss for s in sections):
            sections.append({
                "section": ss,
                "confidence": 0.3,  # Low confidence — inferred, not found
                "match_count": 0,
                "context": ["Standard section — not explicitly mentioned in notice"],
            })

    # Sort by confidence (highest first)
    sections.sort(key=lambda s: s["confidence"], reverse=True)

    return sections


def _extract_criteria(text: str, notice: dict) -> list[dict]:
    """Extract award criteria and weightings."""
    criteria = []
    text_lower = text.lower()

    # Try structured data first
    award_detail = notice.get("award_criteria_detail", "")
    if award_detail:
        text_lower = f"{text_lower} {award_detail.lower()}"

    for pattern, label in CRITERIA_PATTERNS:
        match = re.search(pattern, text_lower)
        if match:
            pct = int(match.group(1))
            # Avoid duplicates
            if not any(c["label"] == label for c in criteria):
                criteria.append({"label": label, "weight_pct": pct})

    # If no percentages found, check for qualitative criteria
    if not criteria:
        award_text = notice.get("award_criteria", "")
        if "most economically advantageous" in text_lower or "meat" in text_lower:
            criteria.append({"label": "quality_and_price", "weight_pct": None,
                           "note": "MEAT criteria — exact split not specified"})
        elif "lowest price" in text_lower:
            criteria.append({"label": "price", "weight_pct": 100,
                           "note": "Lowest price wins"})

    return criteria


def _extract_document_requirements(text: str, notice: dict) -> list[str]:
    """Identify documents the bidder needs to submit."""
    doc_patterns = [
        (r"case stud(?:y|ies)", "Case studies"),
        (r"cv|curriculum vitae", "Team CVs"),
        (r"method statement", "Method statement"),
        (r"project plan", "Project plan"),
        (r"pricing? schedule", "Pricing schedule"),
        (r"rate card", "Rate card"),
        (r"risk register", "Risk register"),
        (r"organis?ation(?:al)? chart", "Organisation chart"),
        (r"certificate|accreditation", "Certificates/accreditations"),
        (r"insurance", "Insurance certificates"),
        (r"references?", "Client references"),
        (r"gantt|timeline|programme", "Delivery timeline"),
        (r"equality|diversity", "Equality & diversity policy"),
        (r"health.+safety|h&s", "Health & safety policy"),
        (r"environmental|sustainability policy", "Environmental policy"),
        (r"data protection|privacy|gdpr", "Data protection policy"),
        (r"cyber essentials", "Cyber Essentials certificate"),
        (r"dbs|disclosure", "DBS checks"),
    ]

    text_lower = text.lower()
    required = []
    for pattern, doc_name in doc_patterns:
        if re.search(pattern, text_lower):
            if doc_name not in required:
                required.append(doc_name)

    return required


def _extract_constraints(text: str, notice: dict) -> dict:
    """Extract word limits, page limits, deadlines, and other constraints."""
    constraints = {}
    text_lower = text.lower()

    # Word limits
    word_match = re.search(r"(\d{2,5})\s*(?:word|words)\s*(?:limit|max|maximum)?", text_lower)
    if word_match:
        constraints["word_limit"] = int(word_match.group(1))

    # Page limits
    page_match = re.search(r"(\d{1,3})\s*(?:page|pages|a4)\s*(?:limit|max|maximum)?", text_lower)
    if page_match:
        constraints["page_limit"] = int(page_match.group(1))

    # Deadline
    constraints["closing_date"] = notice.get("closing_date", "")
    constraints["start_date"] = notice.get("start_date", "")
    constraints["duration_months"] = notice.get("duration_months")

    # Contract duration
    dur_match = re.search(r"(\d{1,3})\s*(?:month|months|week|weeks)", text_lower)
    if dur_match and not constraints.get("duration_months"):
        val = int(dur_match.group(1))
        if "week" in dur_match.group(0):
            val = round(val / 4.3)
        constraints["duration_months"] = val

    return constraints
