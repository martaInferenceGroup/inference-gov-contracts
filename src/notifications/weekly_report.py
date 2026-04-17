"""
Weekly Gov Contracts Email Report
==================================
Searches both portals, filters to criteria, and sends an HTML email
via Microsoft Graph API (sends as your Outlook account).

Run manually:  python -m src.notifications.weekly_report
Scheduled via: GitHub Actions (.github/workflows/weekly-contracts.yml)

Requires environment variables:
    MS_CLIENT_ID      — Azure AD app client ID
    MS_TENANT_ID      — Azure AD tenant ID
    MS_REFRESH_TOKEN  — OAuth2 refresh token (from scripts/get_ms_token.py)
"""

import json
import os
import re
import html as html_mod
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests as http_requests

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

    date_from = datetime(2020, 1, 1)  # no time limit — search all available contracts

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

    # Filter out false positives
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
# Scope summary
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
    """Build a dashboard-style branded HTML email."""

    cf_count = sum(1 for r in results if r.get("source") == "Contracts Finder")
    fat_count = sum(1 for r in results if r.get("source") == "Find a Tender")
    has_val = [r["total_value"] for r in results if r.get("total_value")]
    avg_val = f"&pound;{sum(has_val)/len(has_val):,.0f}" if has_val else "N/A"

    if not results:
        return f"""
        <div style="font-family:Roboto,Arial,sans-serif; max-width:700px; margin:0 auto; padding:20px;">
            <div style="background:linear-gradient(135deg,{BRAND_BLUE},#3d5a73); padding:24px; border-radius:8px; text-align:center;">
                <h1 style="color:white; margin:0; font-size:20px; font-family:Georgia,serif;">Government Contracts Dashboard</h1>
                <p style="color:#70BAD0; margin:8px 0 0; font-size:13px;">No matching open contracts found — {date_range}</p>
            </div>
        </div>
        """

    # Metric cards
    metric_style = (
        "display:inline-block; background:#ffffff; border:1px solid #e2e6ea; "
        f"border-left:4px solid {BRAND_BLUE}; border-radius:8px; padding:12px 20px; "
        "margin:0 8px; min-width:120px; text-align:center;"
    )
    label_style = f"font-size:11px; color:{BRAND_BLUE}; font-weight:500; text-transform:uppercase; letter-spacing:0.5px;"
    value_style = f"font-size:22px; color:{BRAND_BLUE}; font-weight:700; margin-top:4px;"

    # Contract rows
    rows = ""
    for i, r in enumerate(results):
        scope = html_mod.escape(_summarise(r))
        title = html_mod.escape(r.get("title", ""))
        buyer = html_mod.escape(r.get("buyer", ""))
        value = r.get("total_value")
        val_str = f"&pound;{value:,.0f}" if value else "TBC"
        closing = r.get("closing_date", "")
        closing_str = closing if closing and not closing.startswith("0001") else "TBC"
        ct = r.get("ct", "")
        source = r.get("source", "")
        link = r.get("link", "#")
        bg = "#ffffff" if i % 2 == 0 else "#f8f9fa"

        # Closing date urgency
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

        # Type badge
        type_color = BRAND_BLUE if ct == "Contract" else "#00a3ad" if ct == "Tender" else "#705E81"

        rows += f"""
        <tr style="background:{bg};">
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; width:40%;">
                <a href="{link}" style="color:{BRAND_BLUE}; font-weight:600; text-decoration:none; font-size:14px; line-height:1.3;">
                    {title}
                </a>
                <div style="color:{BRAND_GREY}; font-size:12px; margin-top:6px; line-height:1.4;">
                    {scope}
                </div>
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; font-size:13px;">
                {buyer}
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; font-size:13px; white-space:nowrap;">
                {val_str}
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; font-size:13px; white-space:nowrap; {closing_style}">
                {closing_str}
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; font-size:12px; white-space:nowrap;">
                <span style="background:{type_color}15; color:{type_color}; padding:2px 8px; border-radius:10px; font-size:11px;">{ct}</span>
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; font-size:12px; color:{BRAND_GREY}; white-space:nowrap;">
                {source}
            </td>
        </tr>
        """

    return f"""
    <div style="font-family:Roboto,Arial,sans-serif; max-width:960px; margin:0 auto;">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,{BRAND_BLUE},#3d5a73); padding:24px 28px; border-radius:10px 10px 0 0;">
            <h1 style="color:white; margin:0; font-size:22px; font-family:Georgia,serif;">
                Government Contracts Dashboard
            </h1>
            <p style="color:#70BAD0; margin:6px 0 0; font-size:13px;">
                Open AI &amp; Data opportunities under &pound;500k &bull; {date_range} &bull; Sorted by closing date
            </p>
        </div>

        <!-- Metric cards -->
        <div style="background:#f0f2f6; padding:16px 20px; text-align:center;">
            <div style="{metric_style}">
                <div style="{label_style}">Total</div>
                <div style="{value_style}">{len(results)}</div>
            </div>
            <div style="{metric_style}">
                <div style="{label_style}">Contracts Finder</div>
                <div style="{value_style}">{cf_count}</div>
            </div>
            <div style="{metric_style}">
                <div style="{label_style}">Find a Tender</div>
                <div style="{value_style}">{fat_count}</div>
            </div>
            <div style="{metric_style}">
                <div style="{label_style}">Avg Value</div>
                <div style="{value_style}">{avg_val}</div>
            </div>
        </div>

        <!-- Results table -->
        <table style="width:100%; border-collapse:collapse; font-size:13px; background:white;">
            <thead>
                <tr style="background:{BRAND_BLUE}; color:white; text-align:left;">
                    <th style="padding:12px; font-weight:500;">Contract &amp; Summary</th>
                    <th style="padding:12px; font-weight:500;">Buyer</th>
                    <th style="padding:12px; font-weight:500;">Value</th>
                    <th style="padding:12px; font-weight:500;">Closes</th>
                    <th style="padding:12px; font-weight:500;">Type</th>
                    <th style="padding:12px; font-weight:500;">Source</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>

        <!-- Footer -->
        <div style="padding:18px 28px; background:#f8f9fa; border-radius:0 0 10px 10px; text-align:center;">
            <a href="https://inference-gov-contracts.streamlit.app"
               style="display:inline-block; background:{BRAND_ORANGE}; color:white; padding:10px 24px; border-radius:6px; text-decoration:none; font-weight:600; font-size:14px;">
                Open Full Dashboard
            </a>
            <p style="margin:12px 0 0; font-size:11px; color:{BRAND_GREY};">
                Inference Group &bull; Automated Gov Contract Finder &bull; Sent every Friday
            </p>
        </div>
    </div>
    """


# ---------------------------------------------------------------------------
# Microsoft Graph email
# ---------------------------------------------------------------------------

def _get_access_token() -> str:
    """Exchange refresh token for a fresh access token."""
    client_id = os.environ.get("MS_CLIENT_ID", "")
    tenant_id = os.environ.get("MS_TENANT_ID", "")
    refresh_token = os.environ.get("MS_REFRESH_TOKEN", "")

    if not all([client_id, tenant_id, refresh_token]):
        raise RuntimeError(
            "MS_CLIENT_ID, MS_TENANT_ID, and MS_REFRESH_TOKEN environment variables are required."
        )

    resp = http_requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "https://graph.microsoft.com/Mail.Send offline_access",
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Token refresh failed ({resp.status_code}): {resp.text}")

    data = resp.json()

    # Print new refresh token if rotated (for manual update)
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        print("NOTE: Refresh token was rotated. Update MS_REFRESH_TOKEN secret with:")
        print(new_refresh)

    return data["access_token"]


def send_email(html_body: str, recipients: list[str], subject: str, from_email: str):
    """Send HTML email via Microsoft Graph API."""
    access_token = _get_access_token()

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in recipients
            ],
        },
        "saveToSentItems": "true",
    }

    resp = http_requests.post(
        f"https://graph.microsoft.com/v1.0/me/sendMail",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if resp.status_code != 202:
        raise RuntimeError(f"Graph sendMail error ({resp.status_code}): {resp.text}")

    print(f"Email sent to {', '.join(recipients)} via Microsoft Graph")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Weekly Contract Report — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    search_cfg, email_cfg = load_configs()

    results = fetch_and_filter(search_cfg, email_cfg)
    print(f"\n{len(results)} contracts after filtering")

    date_range = datetime.now().strftime('%d %b %Y')

    if results:
        subject = f"{len(results)} Open AI & Data Gov Contracts Under \u00a3500k — {date_range}"
    else:
        subject = f"Weekly Gov Contracts — No New Opportunities — {date_range}"

    html = build_email_html(results, date_range)

    from_email = email_cfg.get("from_email", "marta@inferencegroup.com")
    recipients = email_cfg["recipients"]
    send_email(html, recipients, subject, from_email)

    print("Done.")


if __name__ == "__main__":
    main()
