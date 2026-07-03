"""
Application Drafting Engine
============================
Uses Claude to draft tender responses for Inference Group.

Template-driven: loads application structure from templates/*.json and
reusable content from templates/reusable/. Different procurement routes
(open procedure, restricted SQ/ITT, below-threshold) produce structurally
different applications.

Requires: ANTHROPIC_API_KEY environment variable.
"""

import os
import json
import re
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

ROOT = Path(__file__).parent.parent.parent
TEMPLATES_DIR = ROOT / "templates"
REUSABLE_DIR = TEMPLATES_DIR / "reusable"
APP_TYPES_CONFIG = ROOT / "config" / "application_types.json"


# ---------------------------------------------------------------------------
# Template loading
# ---------------------------------------------------------------------------

def load_company_profile() -> str:
    """Load company profile from reusable content. Falls back to inline."""
    path = REUSABLE_DIR / "company_overview.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    # Fallback — should not happen if templates are set up
    return "Inference Group is a UK-based AI and data consultancy."


def load_template(template_type: str) -> dict:
    """Load an application template by type name."""
    path = TEMPLATES_DIR / f"{template_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_reusable_content(key: str) -> str | None:
    """Load reusable content by key. Returns text or None."""
    path = REUSABLE_DIR / f"{key}.md"
    if path.exists():
        return path.read_text(encoding="utf-8")

    # Check for directory (e.g. case_studies/)
    dir_path = REUSABLE_DIR / key
    if dir_path.is_dir():
        parts = []
        for md_file in sorted(dir_path.glob("*.md")):
            parts.append(md_file.read_text(encoding="utf-8"))
        if parts:
            return "\n\n---\n\n".join(parts)

    return None


def load_detection_rules() -> dict:
    """Load application type detection rules from config."""
    if APP_TYPES_CONFIG.exists():
        with open(APP_TYPES_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("detection_rules", {})
    return {}


def detect_application_type(notice: dict) -> str:
    """Infer the application type from notice text and metadata.

    Returns a template type string (e.g. 'open_procedure').
    """
    rules = load_detection_rules()
    if not rules:
        return "open_procedure"

    # Build searchable text from notice
    text_parts = [
        notice.get("title", ""),
        notice.get("description", ""),
        notice.get("full_description", ""),
        notice.get("notice_type", ""),
        notice.get("procedure_type", ""),
        notice.get("additional_text", ""),
    ]
    text = " ".join(str(p) for p in text_parts if p).lower()
    value = notice.get("total_value") or notice.get("value")

    # Check rules in priority order
    sorted_rules = sorted(rules.items(), key=lambda x: x[1].get("priority", 99))

    for type_name, rule in sorted_rules:
        # Skip the default rule
        if rule.get("priority", 99) >= 99:
            continue

        # Check value threshold
        max_val = rule.get("max_value")
        if max_val and value and value <= max_val:
            # Value match — but also check if keywords strengthen the match
            keywords = rule.get("keywords", [])
            if not keywords or any(kw.lower() in text for kw in keywords):
                return type_name

        # Check keyword matches
        keywords = rule.get("keywords", [])
        if keywords and any(kw.lower() in text for kw in keywords):
            return type_name

    return "open_procedure"


# ---------------------------------------------------------------------------
# Fallback section prompts (used when template guidance is missing)
# ---------------------------------------------------------------------------

FALLBACK_SECTION_PROMPTS = {
    "methodology": "Describe how Inference Group would deliver this contract. Structure as phases (Discovery, Alpha, Beta, Live). Include governance and stakeholder engagement.",
    "experience": "Highlight relevant experience from the leadership team. Structure as 2-3 case study summaries. Be honest about framing founder experience.",
    "team": "Propose a realistic team structure. Name key people with brief bios. Be realistic about team size (~14 people).",
    "social_value": "Address the Social Value Model themes. Include measurable commitments. Keep proportionate to contract value.",
    "quality_assurance": "Describe QA processes for AI/data deliverables. Cover code review, testing, information security, AI-specific quality.",
    "risk_management": "Identify 4-6 realistic risks with mitigations. Include AI-specific and delivery risks. Table format works well.",
    "mobilisation": "Outline the first 2-4 weeks. Cover kick-off, stakeholder mapping, data access, environment setup, knowledge transfer.",
    "innovation": "Describe added value beyond minimum requirements. Reference the Business AI Alliance. Keep proportionate.",
    "pricing": "SKELETON ONLY — mark all figures as [TO BE COMPLETED BY TEAM]. Suggest rate card structure and pricing model.",
    "company_overview": "Standard company introduction. Tailor to the buyer's sector. Keep factual and concise.",
    "exclusion_grounds": "Standard declarations on mandatory and discretionary exclusion grounds. Inference Group should answer 'No' to all mandatory grounds.",
    "financial_standing": "Demonstrate financial viability. Reference insurance coverage and stability.",
    "team_capability": "Summarise key personnel with relevant qualifications. Show team capacity.",
    "policies": "Confirm policies are in place: equality, environmental, GDPR, modern slavery, H&S.",
    "understanding": "Demonstrate understanding of the buyer's problem and context. Show insight beyond restating the spec.",
}


def get_client() -> "anthropic.Anthropic":
    """Create an Anthropic client. Raises if API key is not set."""
    if anthropic is None:
        raise RuntimeError(
            "anthropic package not installed. Run: pip install anthropic"
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable is required for drafting."
        )

    return anthropic.Anthropic(api_key=api_key)


def draft_section(client: "anthropic.Anthropic",
                  section_id: str,
                  requirements: dict,
                  template_section: dict | None = None,
                  word_limit: int | None = None) -> dict:
    """Draft a single section of the tender response.

    Args:
        client: Anthropic client instance.
        section_id: Section key (e.g. "methodology", "experience").
        requirements: Output from requirement_extractor.extract_requirements().
        template_section: Section definition from the template (guidance, word limit, etc.).
        word_limit: Optional word limit override.

    Returns dict with:
        - section: section id
        - section_name: display name
        - content: the drafted text
        - word_count: approximate word count
        - confidence: how confident we are this is relevant (from extraction)
        - reusable: whether this section used reusable content
    """
    ts = template_section or {}
    section_name = ts.get("name", section_id.replace("_", " ").title())
    is_reusable = ts.get("reusable", False)

    # Get guidance: template guidance > fallback prompts
    guidance = ts.get("guidance") or FALLBACK_SECTION_PROMPTS.get(
        section_id, f"Draft a {section_name} section for this tender response."
    )

    # Load reusable content if applicable
    reusable_text = ""
    reusable_key = ts.get("reusable_content_key")
    if reusable_key:
        content = load_reusable_content(reusable_key)
        if content:
            reusable_text = f"\nREUSABLE BASE CONTENT (adapt and build on this — do not copy verbatim):\n{content}\n"

    # Find section context from requirement extraction
    section_info = next(
        (s for s in requirements.get("sections", []) if s["section"] == section_id),
        None
    )
    context_excerpts = ""
    confidence = 0.5
    if section_info:
        confidence = section_info.get("confidence", 0.5)
        if section_info.get("context"):
            context_excerpts = "\n".join(section_info["context"])

    # Build criteria context
    criteria_text = ""
    if requirements.get("criteria"):
        criteria_lines = []
        for c in requirements["criteria"]:
            weight = f"{c['weight_pct']}%" if c.get("weight_pct") else "unspecified"
            note = f" ({c['note']})" if c.get("note") else ""
            criteria_lines.append(f"  - {c['label']}: {weight}{note}")
        criteria_text = "EVALUATION CRITERIA:\n" + "\n".join(criteria_lines)

    # Determine word limit: explicit override > template default > contract constraints
    effective_limit = word_limit or ts.get("default_word_limit")
    word_guidance = ""
    if effective_limit:
        word_guidance = f"\n\nWORD LIMIT: Maximum {effective_limit} words. Be concise."
    elif requirements.get("constraints", {}).get("word_limit"):
        wl = requirements["constraints"]["word_limit"]
        word_guidance = f"\n\nWORD LIMIT: The tender specifies a {wl}-word limit. Stay well within it."

    # Load company profile
    company_profile = load_company_profile()

    prompt = f"""You are drafting a UK government tender response for Inference Group.

CONTRACT DETAILS:
- Title: {requirements.get('title', 'Unknown')}
- Buyer: {requirements.get('buyer', 'Unknown')}
- Value: £{requirements.get('value', 'TBC'):,} if known
- Closing date: {requirements.get('closing_date', 'Unknown')}
- Procedure: {requirements.get('procedure_type', 'Open procedure')}

{criteria_text}

RELEVANT EXCERPTS FROM THE TENDER NOTICE:
{context_excerpts if context_excerpts else 'No specific excerpts available — draft based on standard requirements for this type of contract.'}

COMPANY INFORMATION:
{company_profile}
{reusable_text}

SECTION TO DRAFT: {section_name}

{guidance}
{word_guidance}

FORMATTING:
- Write in third person ("Inference Group will..." not "We will...")
- Use clear, professional UK English
- Number paragraphs or use subheadings for structure
- Be specific and evidence-based, not generic
- Suitable for copy-pasting into a tender portal form
"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    content = response.content[0].text.strip()

    return {
        "section": section_id,
        "section_name": section_name,
        "content": content,
        "word_count": len(content.split()),
        "confidence": confidence,
        "reusable": is_reusable,
    }


def draft_full_application(requirements: dict,
                           template_type: str | None = None,
                           sections: list[str] | None = None) -> dict:
    """Draft all sections of a tender response using the appropriate template.

    Args:
        requirements: Output from requirement_extractor.extract_requirements().
        template_type: Application type (e.g. "open_procedure"). If None,
                       auto-detected from the notice.
        sections: Optional explicit list of section IDs to draft.
                  If None, uses the template's section list.

    Returns dict with:
        - contract: basic contract info
        - template_type: which template was used
        - template_name: display name of the template
        - sections: list of drafted sections
        - documents_required: from template
        - documents_available: from notice
        - constraints: from notice
        - summary: overview of what was drafted
    """
    client = get_client()

    # Load template
    if template_type is None:
        template_type = detect_application_type(requirements)
    print(f"  Application type: {template_type}")

    try:
        template = load_template(template_type)
    except FileNotFoundError:
        print(f"  WARNING: Template '{template_type}' not found, falling back to open_procedure")
        template_type = "open_procedure"
        template = load_template(template_type)

    template_name = template.get("name", template_type)
    template_sections = {s["id"]: s for s in template.get("sections", [])}

    # Determine which sections to draft
    if sections is None:
        # Use template section order — draft required sections, plus optional
        # ones that the requirement extractor found evidence for
        extracted_ids = {s["section"] for s in requirements.get("sections", [])}
        sections = []
        for ts in template.get("sections", []):
            if ts.get("required") or ts["id"] in extracted_ids:
                sections.append(ts["id"])

    # Draft each section
    drafted = []
    for section_id in sections:
        ts = template_sections.get(section_id)
        section_name = ts["name"] if ts else section_id.replace("_", " ").title()
        print(f"  Drafting: {section_name}...")
        try:
            result = draft_section(client, section_id, requirements,
                                   template_section=ts)
            drafted.append(result)
            print(f"    Done ({result['word_count']} words)")
        except Exception as e:
            print(f"    Error drafting {section_name}: {e}")
            drafted.append({
                "section": section_id,
                "section_name": section_name,
                "content": f"[ERROR: Could not draft this section — {e}]",
                "word_count": 0,
                "confidence": 0,
                "reusable": False,
            })

    # Merge document requirements from template + extraction
    template_docs = template.get("documents_typically_required", [])
    extracted_docs = requirements.get("documents_required", [])
    all_docs = list(dict.fromkeys(template_docs + extracted_docs))  # dedup, preserve order

    # Check for [REQUIRED: ...] placeholders in reusable content that made it into drafts
    missing_data_warnings = []
    for s in drafted:
        import re as _re
        required_tags = _re.findall(r"\[REQUIRED:\s*([^\]]+)\]", s.get("content", ""))
        for tag in required_tags:
            missing_data_warnings.append(f"Section '{s['section_name']}': {tag.strip()}")

    if missing_data_warnings:
        print(f"  WARNING: {len(missing_data_warnings)} missing company data fields detected in draft")
        for w in missing_data_warnings:
            print(f"    - {w}")

    return {
        "contract": {
            "title": requirements.get("title", ""),
            "buyer": requirements.get("buyer", ""),
            "value": requirements.get("value"),
            "closing_date": requirements.get("closing_date", ""),
            "link": requirements.get("link", ""),
        },
        "template_type": template_type,
        "template_name": template_name,
        "sections": drafted,
        "documents_required": all_docs,
        "documents_available": requirements.get("documents_available", []),
        "constraints": requirements.get("constraints", {}),
        "missing_data_warnings": missing_data_warnings,
        "summary": {
            "total_sections": len(drafted),
            "total_words": sum(s["word_count"] for s in drafted),
            "sections_drafted": [s["section"] for s in drafted],
            "missing_data_count": len(missing_data_warnings),
        },
    }
