"""
Draft Application Email Sender
================================
Formats drafted tender responses as a structured HTML email and sends
via Microsoft Graph. Designed to be copy-paste friendly for portal forms.

Uses the same MS Graph infrastructure as the weekly report.
"""

import html as html_mod
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.notifications.weekly_report import send_email_with_retry

BRAND_BLUE = "#30475E"
BRAND_ORANGE = "#D08770"
BRAND_GREY = "#6b7785"


def build_draft_email(application: dict) -> str:
    """Build HTML email for a drafted application.

    Args:
        application: Output from drafter.draft_full_application().

    Returns HTML string.
    """
    contract = application["contract"]
    sections = application["sections"]
    docs_required = application.get("documents_required", [])
    docs_available = application.get("documents_available", [])
    constraints = application.get("constraints", {})
    summary = application.get("summary", {})

    title = html_mod.escape(contract.get("title", ""))
    buyer = html_mod.escape(contract.get("buyer", ""))
    value = contract.get("value")
    value_str = f"&pound;{value:,.0f}" if value else "TBC"
    closing = contract.get("closing_date", "TBC")
    link = contract.get("link", "")

    # Template info
    template_type = application.get("template_type", "")
    template_name = html_mod.escape(application.get("template_name", ""))

    # Section HTML
    sections_html = ""
    for i, s in enumerate(sections, 1):
        section_name = s.get("section_name", s["section"].replace("_", " ").title())
        confidence = s.get("confidence", 0)
        word_count = s.get("word_count", 0)
        content = s.get("content", "")
        is_reusable = s.get("reusable", False)

        # Confidence indicator
        if confidence >= 0.7:
            conf_label = "High relevance"
            conf_color = "#1a7a3a"
        elif confidence >= 0.4:
            conf_label = "Moderate relevance"
            conf_color = BRAND_ORANGE
        else:
            conf_label = "Inferred section"
            conf_color = BRAND_GREY

        # Convert content newlines to HTML paragraphs
        content_html = ""
        for para in content.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            # Preserve single newlines as <br>
            para_html = html_mod.escape(para).replace("\n", "<br>")
            content_html += f'<p style="margin:0 0 10px 0; line-height:1.6;">{para_html}</p>'

        sections_html += f"""
        <div style="margin-bottom:24px; border:1px solid #e2e6ea; border-radius:8px; overflow:hidden;">
            <div style="background:{BRAND_BLUE}; padding:12px 16px; display:flex; justify-content:space-between; align-items:center;">
                <span style="color:white; font-weight:600; font-size:15px;">
                    {i}. {section_name}
                </span>
                <span style="font-size:11px;">
                    {f'<span style="color:#2e9e50; background:white; padding:2px 8px; border-radius:10px; margin-right:4px;">Reusable</span>' if is_reusable else ''}
                    <span style="color:{conf_color}; background:white; padding:2px 8px; border-radius:10px; margin-right:8px;">{conf_label}</span>
                    <span style="color:#b0b0b0;">{word_count} words</span>
                </span>
            </div>
            <div style="padding:16px; font-size:13px; color:#333; font-family:Roboto,Arial,sans-serif;">
                {content_html}
            </div>
        </div>
        """

    # Documents checklist
    docs_html = ""
    if docs_required:
        doc_items = ""
        for doc in docs_required:
            doc_items += f'<li style="margin:4px 0; font-size:13px;">{html_mod.escape(doc)}</li>'
        docs_html = f"""
        <div style="margin-bottom:24px; border:1px solid #e2e6ea; border-radius:8px; overflow:hidden;">
            <div style="background:{BRAND_ORANGE}; padding:12px 16px;">
                <span style="color:white; font-weight:600; font-size:15px;">Documents Checklist</span>
            </div>
            <div style="padding:16px;">
                <ul style="margin:0; padding-left:20px;">{doc_items}</ul>
            </div>
        </div>
        """

    # Available tender documents
    avail_docs_html = ""
    if docs_available:
        avail_items = ""
        for doc in docs_available:
            name = html_mod.escape(doc.get("name", "Document"))
            url = doc.get("url", "")
            if url:
                avail_items += f'<li style="margin:4px 0; font-size:13px;"><a href="{url}" style="color:{BRAND_BLUE};">{name}</a></li>'
            else:
                avail_items += f'<li style="margin:4px 0; font-size:13px;">{name}</li>'
        avail_docs_html = f"""
        <div style="margin-bottom:24px; border:1px solid #e2e6ea; border-radius:8px; overflow:hidden;">
            <div style="background:{BRAND_BLUE}; padding:12px 16px;">
                <span style="color:white; font-weight:600; font-size:15px;">Tender Documents</span>
            </div>
            <div style="padding:16px;">
                <ul style="margin:0; padding-left:20px;">{avail_items}</ul>
            </div>
        </div>
        """

    # Constraints info
    constraint_html = ""
    if constraints:
        constraint_items = ""
        if constraints.get("word_limit"):
            constraint_items += f'<span style="margin-right:16px;">Word limit: {constraints["word_limit"]}</span>'
        if constraints.get("page_limit"):
            constraint_items += f'<span style="margin-right:16px;">Page limit: {constraints["page_limit"]}</span>'
        if constraints.get("duration_months"):
            constraint_items += f'<span style="margin-right:16px;">Duration: {constraints["duration_months"]} months</span>'
        if constraint_items:
            constraint_html = f"""
            <div style="background:#f0f2f6; padding:10px 16px; border-radius:6px; margin-bottom:20px; font-size:12px; color:{BRAND_GREY};">
                {constraint_items}
            </div>
            """

    return f"""
    <div style="font-family:Roboto,Arial,sans-serif; max-width:800px; margin:0 auto;">
        <!-- DRAFT Banner -->
        <div style="background:#dc3545; padding:10px; text-align:center; border-radius:8px 8px 0 0;">
            <span style="color:white; font-weight:700; font-size:14px; letter-spacing:1px;">
                DRAFT APPLICATION — REVIEW BEFORE SUBMITTING
            </span>
        </div>

        <!-- Header -->
        <div style="background:linear-gradient(135deg,{BRAND_BLUE},#3d5a73); padding:24px 28px;">
            <h1 style="color:white; margin:0; font-size:20px; font-family:Georgia,serif;">
                {title}
            </h1>
            <p style="color:#70BAD0; margin:8px 0 0; font-size:13px;">
                {buyer} &bull; {value_str} &bull; Closes {closing}
            </p>
            {f'<p style="margin:8px 0 0;"><a href="{link}" style="color:{BRAND_ORANGE}; font-size:12px;">View original notice</a></p>' if link else ''}
        </div>

        <!-- Summary -->
        <div style="background:#f0f2f6; padding:14px 28px; font-size:13px; color:{BRAND_BLUE};">
            {f'<span style="background:{BRAND_BLUE}10; border:1px solid {BRAND_BLUE}30; padding:2px 10px; border-radius:10px; font-size:11px; margin-right:8px;">{template_name}</span>' if template_name else ''}
            <strong>{summary.get('total_sections', 0)}</strong> sections drafted &bull;
            <strong>{summary.get('total_words', 0):,}</strong> total words &bull;
            Generated {datetime.now().strftime('%d %b %Y %H:%M')}
        </div>

        {constraint_html}

        {"" if not application.get("missing_data_warnings") else f'''
        <div style="padding:0 28px;">
            <div style="background:#f8d7da; border-left:4px solid #dc3545; padding:12px 16px; border-radius:6px; margin-bottom:16px;">
                <div style="font-weight:700; color:#721c24; font-size:13px; margin-bottom:6px;">
                    Missing Company Data ({len(application["missing_data_warnings"])} items)
                </div>
                <ul style="margin:0; padding-left:20px; font-size:12px; color:#721c24;">
                    {"".join(f"<li>{html_mod.escape(w)}</li>" for w in application["missing_data_warnings"])}
                </ul>
            </div>
        </div>
        '''}

        <!-- Content -->
        <div style="padding:20px 28px;">
            <p style="font-size:13px; color:{BRAND_GREY}; margin:0 0 20px 0; padding:12px; background:#fff3cd; border-radius:6px; border-left:4px solid {BRAND_ORANGE};">
                Each section below is drafted for copy-pasting into the tender portal.
                Review and customise before submission — particularly the experience
                section (verify all claims) and pricing (add actual figures).
            </p>

            {sections_html}
            {docs_html}
            {avail_docs_html}
        </div>

        <!-- Footer -->
        <div style="padding:18px 28px; background:#f8f9fa; border-radius:0 0 8px 8px; text-align:center;">
            <p style="margin:0; font-size:11px; color:{BRAND_GREY};">
                Inference Group &bull; Auto-drafted application &bull; Review before submitting
            </p>
        </div>
    </div>
    """


def send_draft(application: dict, recipients: list[str],
               from_email: str = "marta@inferencegroup.com") -> None:
    """Build and send the draft application email.

    Args:
        application: Output from drafter.draft_full_application().
        recipients: Email addresses to send to.
        from_email: Sender address.
    """
    html = build_draft_email(application)

    title = application["contract"].get("title", "Unknown Contract")
    subject = f"Draft Application: {title[:60]}"

    send_email_with_retry(html, recipients, subject, from_email)
    print(f"Draft application sent to {', '.join(recipients)}")
