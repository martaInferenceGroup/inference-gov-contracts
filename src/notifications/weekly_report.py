"""
Weekly Gov Contracts Email Report
==================================
Searches both portals, filters to criteria, and sends an HTML email.

Run manually:  python -m src.notifications.weekly_report
Scheduled via: GitHub Actions (.github/workflows/weekly-contracts.yml)

Requires environment variables:
    SMTP_HOST      — e.g. smtp.office365.com
    SMTP_PORT      — e.g. 587
    SMTP_USER      — e.g. marta@inferencegroup.com
    SMTP_PASSWORD  — app password or account password
"""

import json
import os
import re
import html as html_mod
import smtplib
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.scrapers import contracts_finder, find_a_tender
from src.analysis.qc_agents import audit_keyword_relevance

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent.parent
SEARCH_CONFIG = ROOT / "config" / "search_criteria.json"
EMAIL_CONFIG = ROOT / "config" / "email_criteria.json"


def load_configs() -> tuple[dict, dict]:
    with open(SEARCH_CONFIG) as f:
        search = json.load(f)
    with open(EMAIL_CONFIG) as f:
        email = json.load(f)
    return search, email


# ---------------------------------------------------------------------------
# Fetch & filter
# ---------------------------------------------------------------------------

def fetch_and_filter(search_cfg: dict, email_cfg: dict) -> list[dict]:
    """Fetch from both portals and apply email criteria."""
    keywords = search_cfg["keywords"]
    cpv_codes = search_cfg.get("cpv_codes", [])
    criteria = email_cfg["criteria"]
    max_value = criteria.get("max_value")
    open_only = criteria.get("open_only", True)

    date_from = datetime.now() - timedelta(days=7)  # last week

    all_results = []

    # --- Contracts Finder ---
    try:
        statuses = ["Open"] if open_only else None
        cf_results, _ = contracts_finder.fetch_notices(
            keywords=keywords,
            published_from=date_from,
            max_value=max_value,
            statuses=statuses,
            cpv_codes=cpv_codes,
        )
        all_results.extend(cf_results)
        print(f"Contracts Finder: {len(cf_results)} results")
    except Exception as e:
        print(f"Contracts Finder error: {e}")

    # --- Find a Tender ---
    try:
        stages = ["tender", "planning"] if open_only else None
        fat_results = find_a_tender.fetch_notices(
            keywords=keywords,
            stages=stages,
            max_value=max_value,
            published_from=date_from,
            max_pages=3,
        )
        all_results.extend(fat_results)
        print(f"Find a Tender: {len(fat_results)} results")
    except Exception as e:
        print(f"Find a Tender error: {e}")

    # Deduplicate
    seen = set()
    unique = []
    for r in all_results:
        key = r.get("ocid", "")
        if key and key not in seen:
            seen.add(key)
            unique.append(r)

    # Filter out false positives using QC agent
    relevance = audit_keyword_relevance(unique)
    filtered = []
    for r, rel in zip(unique, relevance):
        if not rel["is_false_positive"]:
            filtered.append(r)
        else:
            print(f"  Filtered out (false positive): {r['title'][:50]}")

    # Sort by closing date (earliest first), blanks at end
    def sort_key(r):
        d = r.get("closing_date", "") or ""
        if not d or d.startswith("0001"):
            return "9999-99-99"
        return d

    filtered.sort(key=sort_key)

    return filtered


# ---------------------------------------------------------------------------
# Build scope summary (same logic as dashboard)
# ---------------------------------------------------------------------------

_NOISE = re.compile(
    r"(\*{3,}[^*]*\*{3,}"
    r"|please\s+note[:\s][^.]*\."
    r"|this\s+is\s+(a\s+)?contract\s+award\s+notice[^.]*\."
    r"|this\s+procurement\s+is\s+being\s+concluded[^.]*\."
    r"|contract\s+period:\s*[^.]*\.?"
    r"|total\s+award\s+value[^.]*\.)",
    re.IGNORECASE,
)


def _summarise(r: dict) -> str:
    desc = r.get("description", "")
    title = r.get("title", "")

    if not desc or not desc.strip():
        return title

    text = re.sub(r"<[^>]+>", " ", desc)
    text = html_mod.unescape(text).replace("&amp;", "&")
    text = re.sub(r"\.{3,}", " ", text)
    text = re.sub(r"^[\s.]+", "", text)
    text = _NOISE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip()

    if not text:
        return title

    sentences = re.split(r"(?<=[.!?])\s+", text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 10]

    scope_words = (
        "deliver", "develop", "provid", "build", "design", "creat",
        "implement", "deploy", "support", "consult", "advis", "research",
        "analys", "automat", "model", "platform", "framework", "system",
        "solution", "service", "strategy", "roadmap", "discovery", "pilot",
    )
    skip_words = (
        "have been appointed", "has been appointed", "has been awarded",
        "award of contract",
    )
    for s in sentences:
        sl = s.lower()
        if any(k in sl for k in skip_words):
            continue
        if any(w in sl for w in scope_words):
            words = s.split()
            return " ".join(words[:30]).rstrip(".") + ("..." if len(words) > 30 else "")

    for s in sentences:
        if len(s.split()) >= 5:
            words = s.split()
            return " ".join(words[:30]).rstrip(".") + ("..." if len(words) > 30 else "")

    return title


# ---------------------------------------------------------------------------
# HTML email
# ---------------------------------------------------------------------------

BRAND_BLUE = "#30475E"
BRAND_ORANGE = "#D08770"
BRAND_GREY = "#6b7785"


def build_email_html(results: list[dict], date_range: str) -> str:
    """Build a branded HTML email with the contract list."""

    if not results:
        return f"""
        <div style="font-family:Roboto,Arial,sans-serif; max-width:700px; margin:0 auto; padding:20px;">
            <div style="background:{BRAND_BLUE}; padding:20px; border-radius:8px; text-align:center;">
                <h1 style="color:white; margin:0; font-size:20px;">Inference Group — Weekly Contracts</h1>
                <p style="color:{BRAND_GREY}; margin:5px 0 0;">No new matching contracts found this week ({date_range})</p>
            </div>
        </div>
        """

    rows = ""
    for i, r in enumerate(results):
        scope = html_mod.escape(_summarise(r))
        title = html_mod.escape(r.get("title", ""))
        buyer = html_mod.escape(r.get("buyer", ""))
        value = r.get("total_value")
        val_str = f"&pound;{value:,.0f}" if value else "TBC"
        closing = r.get("closing_date", "")
        closing_str = closing if closing and not closing.startswith("0001") else "TBC"
        source = r.get("source", "")
        link = r.get("link", "#")
        bg = "#ffffff" if i % 2 == 0 else "#f8f9fa"

        # Closing date urgency colour
        closing_style = f"color:{BRAND_GREY};"
        if closing and not closing.startswith("0001") and not closing.startswith("9999"):
            try:
                days_left = (datetime.strptime(closing[:10], "%Y-%m-%d") - datetime.now()).days
                if days_left <= 7:
                    closing_style = "color:#dc3545; font-weight:bold;"
                elif days_left <= 14:
                    closing_style = f"color:{BRAND_ORANGE}; font-weight:bold;"
            except ValueError:
                pass

        rows += f"""
        <tr style="background:{bg};">
            <td style="padding:12px; border-bottom:1px solid #eee; vertical-align:top;">
                <a href="{link}" style="color:{BRAND_BLUE}; font-weight:600; text-decoration:none; font-size:14px;">
                    {title}
                </a>
                <div style="color:{BRAND_GREY}; font-size:12px; margin-top:4px;">
                    {scope}
                </div>
            </td>
            <td style="padding:12px; border-bottom:1px solid #eee; vertical-align:top; white-space:nowrap; font-size:13px;">
                {buyer}
            </td>
            <td style="padding:12px; border-bottom:1px solid #eee; vertical-align:top; white-space:nowrap; font-size:13px;">
                {val_str}
            </td>
            <td style="padding:12px; border-bottom:1px solid #eee; vertical-align:top; white-space:nowrap; font-size:13px; {closing_style}">
                {closing_str}
            </td>
            <td style="padding:12px; border-bottom:1px solid #eee; vertical-align:top; white-space:nowrap; font-size:12px; color:{BRAND_GREY};">
                {source}
            </td>
        </tr>
        """

    return f"""
    <div style="font-family:Roboto,Arial,sans-serif; max-width:900px; margin:0 auto; padding:20px;">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,{BRAND_BLUE},#3d5a73); padding:20px 24px; border-radius:8px 8px 0 0;">
            <h1 style="color:white; margin:0; font-size:20px; font-family:Georgia,serif;">
                Government Contracts — Weekly Report
            </h1>
            <p style="color:#70BAD0; margin:5px 0 0; font-size:13px;">
                {len(results)} matching opportunities &bull; {date_range} &bull; Sorted by closing date
            </p>
        </div>

        <!-- Summary bar -->
        <div style="background:#f0f2f6; padding:12px 24px; display:flex; gap:30px; font-size:13px; color:{BRAND_BLUE};">
            <span><strong>{len(results)}</strong> opportunities</span>
            <span><strong>{sum(1 for r in results if r.get('source')=='Contracts Finder')}</strong> from CF</span>
            <span><strong>{sum(1 for r in results if r.get('source')=='Find a Tender')}</strong> from FaT</span>
        </div>

        <!-- Table -->
        <table style="width:100%; border-collapse:collapse; font-size:13px;">
            <thead>
                <tr style="background:{BRAND_BLUE}; color:white; text-align:left;">
                    <th style="padding:10px 12px; font-weight:500;">Contract</th>
                    <th style="padding:10px 12px; font-weight:500;">Buyer</th>
                    <th style="padding:10px 12px; font-weight:500;">Value</th>
                    <th style="padding:10px 12px; font-weight:500;">Closes</th>
                    <th style="padding:10px 12px; font-weight:500;">Source</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>

        <!-- Footer -->
        <div style="padding:16px 24px; background:#f8f9fa; border-radius:0 0 8px 8px; text-align:center;">
            <p style="margin:0; font-size:12px; color:{BRAND_GREY};">
                Inference Group &bull; Automated Gov Contract Finder &bull;
                <a href="https://inference-gov-contracts.streamlit.app" style="color:{BRAND_ORANGE};">
                    Open Dashboard
                </a>
            </p>
        </div>
    </div>
    """


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

def send_email(html_body: str, recipients: list[str], subject: str):
    """Send HTML email via SMTP."""
    smtp_host = os.environ.get("SMTP_HOST", "smtp.office365.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")

    if not smtp_user or not smtp_pass:
        raise RuntimeError(
            "SMTP_USER and SMTP_PASSWORD environment variables are required. "
            "Set them as GitHub Actions secrets."
        )

    msg = MIMEMultipart("alternative")
    msg["From"] = smtp_user
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    # Plain text fallback
    plain = "View this email in an HTML-compatible email client, or open the dashboard."
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, recipients, msg.as_string())

    print(f"Email sent to {', '.join(recipients)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Weekly Contract Report — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    search_cfg, email_cfg = load_configs()

    results = fetch_and_filter(search_cfg, email_cfg)
    print(f"\n{len(results)} contracts after filtering")

    date_range = f"{(datetime.now() - timedelta(days=7)).strftime('%d %b')} — {datetime.now().strftime('%d %b %Y')}"
    subject = f"{email_cfg['subject_prefix']} — {len(results)} opportunities ({date_range})"

    html = build_email_html(results, date_range)

    recipients = email_cfg["recipients"]
    send_email(html, recipients, subject)

    print("Done.")


if __name__ == "__main__":
    main()
