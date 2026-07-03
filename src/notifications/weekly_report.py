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
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests as http_requests

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.scrapers import contracts_finder, find_a_tender
from src.analysis.qc_agents import (audit_keyword_relevance,
                                     audit_data_completeness,
                                     audit_duplicates)
from src.analysis.fit_scoring import score_all as score_fit
from src.tracking.store import (load_tracking, save_tracking, merge_results,
                                get_pipeline_contracts, get_new_contract_ids,
                                mark_seen, save_weekly_snapshot)

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
# Date normalisation
# ---------------------------------------------------------------------------

def _normalise_date(raw: str) -> str:
    """Normalise a date string to YYYY-MM-DD for comparison.

    Handles:
      - Already YYYY-MM-DD (or with trailing time): "2026-05-11"
      - UK long format: "31 March 2026", "7 May 2026, 11:59pm"
      - Ordinal suffixes: "1st March 2026", "22nd April 2026"
      - Placeholder dates starting with "0001" → ignored

    Returns the YYYY-MM-DD string, or empty string if unparseable.
    """
    if not raw or not raw.strip() or raw.startswith("0001"):
        return ""

    s = raw.strip()

    # Already ISO format
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    # Strip trailing time: ", 11:59pm", " 10:00am", etc.
    s = re.sub(r",?\s*\d{1,2}[:.]\d{2}\s*(?:am|pm|AM|PM)?$", "", s)
    # Strip ordinal suffixes: 1st, 2nd, 3rd, 4th, etc.
    s = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", s)

    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue

    return ""


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

    days_back = search_cfg.get("default_filters", {}).get("published_days_back", 30)
    date_from = datetime.now() - timedelta(days=days_back)

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
    # Don't pass max_value to FaT — many contracts have no value listed
    # or are frameworks with high total values but small call-offs.
    # We filter by value after fetching instead.
    try:
        stages = ["tender", "planning"] if open_only else None
        fat_results = find_a_tender.fetch_notices(
            keywords=keywords,
            stages=stages,
            published_from=date_from,
            max_pages=3,
        )
        # Post-fetch value filter: keep if value is None/0 (unknown) or within range
        if max_value:
            fat_results = [
                r for r in fat_results
                if not r.get("total_value") or r["total_value"] <= max_value
            ]
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

    # Filter out expired deadlines (closing date in the past)
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    not_expired = []
    for r in unique:
        closing_normalized = _normalise_date(r.get("closing_date", "") or "")
        if closing_normalized and closing_normalized < today_str:
            print(f"  Filtered out (expired {closing_normalized}): {r['title'][:50]}")
            continue
        not_expired.append(r)
    print(f"  {len(unique) - len(not_expired)} expired contracts removed")

    # Filter out false positives
    relevance = audit_keyword_relevance(not_expired)
    filtered = []
    for r, rel in zip(not_expired, relevance):
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


def build_qc_flags(results: list[dict]) -> dict[str, list[str]]:
    """Run QC agents and return per-contract warning flags for the email.

    Returns dict of ocid -> list of short warning strings.
    """
    flags: dict[str, list[str]] = {}

    # Completeness check
    completeness = audit_data_completeness(results)
    for finding in completeness:
        ocid = finding.get("ocid", "")
        if finding["completeness_score"] < 7 and finding["missing_fields"]:
            missing = ", ".join(finding["missing_fields"][:3])
            flags.setdefault(ocid, []).append(f"Missing: {missing}")

    # Duplicate check
    dup_groups = audit_duplicates(results)
    for group in dup_groups:
        for notice in group["notices"]:
            ocid = notice.get("ocid", "")
            flags.setdefault(ocid, []).append("Possible duplicate")

    return flags


def _build_pipeline_html(pipeline: dict[str, list[dict]]) -> str:
    """Build the 'Your Pipeline' section showing active bids."""
    if not pipeline:
        return ""

    STATUS_LABELS = {
        "shortlisted": ("Shortlisted", "#705E81"),
        "drafting": ("Drafting", BRAND_ORANGE),
        "draft_sent": ("Draft Ready", "#00a3ad"),
        "submitted": ("Submitted", BRAND_BLUE),
        "won": ("Won", "#1a7a3a"),
    }

    items_html = ""
    for status, contracts in pipeline.items():
        label, color = STATUS_LABELS.get(status, (status.title(), BRAND_GREY))
        for c in contracts:
            title = html_mod.escape(c.get("title", "")[:60])
            buyer = html_mod.escape(c.get("buyer", ""))
            closing = c.get("closing_date", "")
            items_html += (
                f'<div style="padding:6px 0; border-bottom:1px solid #eee;">'
                f'<span style="background:{color}20; color:{color}; padding:2px 8px; '
                f'border-radius:10px; font-size:10px; font-weight:600;">{label}</span> '
                f'<span style="font-weight:600; font-size:13px;">{title}</span>'
                f'<span style="color:{BRAND_GREY}; font-size:12px;"> — {buyer}'
                f'{f" — closes {closing}" if closing else ""}</span>'
                f'</div>'
            )

    return f"""
        <div style="background:#f8faf8; border-left:4px solid #1a7a3a; padding:14px 20px; margin:0;">
            <div style="font-size:13px; font-weight:700; color:{BRAND_BLUE}; margin-bottom:8px;">
                YOUR PIPELINE
            </div>
            {items_html}
        </div>
    """


def build_email_html(results: list[dict], date_range: str,
                     new_ids: set[str] | None = None,
                     pipeline: dict[str, list[dict]] | None = None,
                     fit_scores: dict[str, int] | None = None,
                     qc_flags: dict[str, list[str]] | None = None,
                     draft_trigger_url: str = "") -> str:
    """Build a dashboard-style branded HTML email.

    Args:
        results: Filtered contract list to display.
        date_range: Human-readable date string for the header.
        new_ids: Set of OCIDs that are new this week (get NEW badge).
        pipeline: Dict of status -> contracts for the pipeline section.
        fit_scores: Dict of OCID -> 1-5 fit score.
        qc_flags: Dict of OCID -> list of short warning strings.
        draft_trigger_url: Base URL for one-click draft triggers (Cloudflare Worker).
    """
    new_ids = new_ids or set()
    pipeline = pipeline or {}
    fit_scores = fit_scores or {}
    qc_flags = qc_flags or {}
    date_range_iso = datetime.now().strftime("%Y-%m-%d")

    cf_count = sum(1 for r in results if r.get("source") == "Contracts Finder")
    fat_count = sum(1 for r in results if r.get("source") == "Find a Tender")
    has_val = [r["total_value"] for r in results if r.get("total_value")]
    avg_val = f"&pound;{sum(has_val)/len(has_val):,.0f}" if has_val else "N/A"

    # Expiring soon counts
    now = datetime.now()
    closing_2wk = 0
    for r in results:
        cd_norm = _normalise_date(r.get("closing_date", ""))
        if cd_norm:
            try:
                days_left = (datetime.strptime(cd_norm, "%Y-%m-%d") - now).days
                if 0 <= days_left <= 14:
                    closing_2wk += 1
            except ValueError:
                pass

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
    def _metric_card_style(color: str = BRAND_BLUE) -> str:
        return (
            "display:inline-block; background:#ffffff; border:1px solid #e2e6ea; "
            f"border-left:4px solid {color}; border-radius:8px; padding:12px 20px; "
            "margin:0 8px; min-width:120px; text-align:center;"
        )

    def _label_style(color: str = BRAND_BLUE) -> str:
        return f"font-size:11px; color:{color}; font-weight:500; text-transform:uppercase; letter-spacing:0.5px;"

    def _value_style(color: str = BRAND_BLUE) -> str:
        return f"font-size:22px; color:{color}; font-weight:700; margin-top:4px;"

    metric_style = _metric_card_style()
    label_style = _label_style()
    value_style = _value_style()

    # Urgency styling for "closing soon" card
    urgent_color = BRAND_ORANGE if closing_2wk > 0 else BRAND_BLUE
    urgent_metric = _metric_card_style(urgent_color)
    urgent_label = _label_style(urgent_color)
    urgent_value = _value_style(urgent_color)

    # Fit score colours
    FIT_COLORS = {5: "#1a7a3a", 4: "#2e9e50", 3: BRAND_ORANGE, 2: "#999", 1: "#bbb"}

    # Contract rows
    rows = ""
    for i, r in enumerate(results):
        num = i + 1
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
        ocid = r.get("ocid", "")
        bg = "#ffffff" if i % 2 == 0 else "#f8f9fa"

        # NEW badge
        new_badge = ""
        if ocid in new_ids:
            new_badge = (
                f' <span style="background:#2e9e50; color:white; padding:1px 6px; '
                f'border-radius:8px; font-size:10px; font-weight:600; vertical-align:middle;">NEW</span>'
            )

        # Fit score badge
        fit_badge = ""
        score = fit_scores.get(ocid)
        if score is not None:
            fc = FIT_COLORS.get(score, BRAND_GREY)
            fit_badge = (
                f'<span style="display:inline-block; background:{fc}; color:white; '
                f'width:22px; height:22px; line-height:22px; border-radius:50%; '
                f'text-align:center; font-size:11px; font-weight:700;">{score}</span> '
            )

        # Closing date urgency
        closing_style = f"color:{BRAND_GREY};"
        closing_norm = _normalise_date(closing)
        if closing_norm:
            try:
                days_left = (datetime.strptime(closing_norm, "%Y-%m-%d") - datetime.now()).days
                if days_left <= 7:
                    closing_style = "color:#dc3545; font-weight:bold;"
                elif days_left <= 14:
                    closing_style = f"color:{BRAND_ORANGE}; font-weight:bold;"
            except ValueError:
                pass

        # QC warning badges
        qc_html = ""
        flags = qc_flags.get(ocid, [])
        for flag in flags:
            qc_html += (
                f'<span style="background:#fff3cd; color:#856404; padding:1px 6px; '
                f'border-radius:8px; font-size:10px; margin-right:4px;">{html_mod.escape(flag)}</span>'
            )

        # Type badge
        type_color = BRAND_BLUE if ct == "Contract" else "#00a3ad" if ct == "Tender" else "#705E81"

        rows += f"""
        <tr style="background:{bg};">
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; width:5%; text-align:center; font-size:13px; color:{BRAND_GREY}; font-weight:600;">
                #{num}
            </td>
            <td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; width:38%;">
                {fit_badge}<a href="{link}" style="color:{BRAND_BLUE}; font-weight:600; text-decoration:none; font-size:14px; line-height:1.3;">
                    {title}
                </a>{new_badge}
                <div style="color:{BRAND_GREY}; font-size:12px; margin-top:6px; line-height:1.4;">
                    {scope}
                </div>
                {f'<div style="margin-top:4px;">{qc_html}</div>' if qc_html else ''}
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
            {f"""<td style="padding:14px 12px; border-bottom:1px solid #eee; vertical-align:top; text-align:center;">
                <a href="{draft_trigger_url}/draft?contract={num}&week={date_range_iso}" target="_blank"
                   style="display:inline-block; background:{BRAND_ORANGE}; color:white; padding:6px 12px;
                   border-radius:6px; text-decoration:none; font-size:11px; font-weight:600;
                   white-space:nowrap;">Draft&nbsp;#{num}</a>
            </td>""" if draft_trigger_url else ""}
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
            <div style="{urgent_metric}">
                <div style="{urgent_label}">Closing &lt;2 Weeks</div>
                <div style="{urgent_value}">{closing_2wk}</div>
            </div>
        </div>

        {_build_pipeline_html(pipeline)}

        <!-- Results table -->
        <table style="width:100%; border-collapse:collapse; font-size:13px; background:white;">
            <thead>
                <tr style="background:{BRAND_BLUE}; color:white; text-align:left;">
                    <th style="padding:12px; font-weight:500; width:5%;">#</th>
                    <th style="padding:12px; font-weight:500;">Contract &amp; Summary</th>
                    <th style="padding:12px; font-weight:500;">Buyer</th>
                    <th style="padding:12px; font-weight:500;">Value</th>
                    <th style="padding:12px; font-weight:500;">Closes</th>
                    <th style="padding:12px; font-weight:500;">Type</th>
                    <th style="padding:12px; font-weight:500;">Source</th>
                    {f'<th style="padding:12px; font-weight:500;">Action</th>' if draft_trigger_url else ''}
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>

        {"" if not draft_trigger_url else f"""
        <!-- Draft instructions -->
        <div style="background:#f0f2f6; padding:14px 28px; border-top:1px solid #e2e6ea;">
            <p style="margin:0; font-size:12px; color:{BRAND_BLUE}; line-height:1.5;">
                <strong>To draft an application:</strong> Click any
                <span style="background:{BRAND_ORANGE}; color:white; padding:1px 6px; border-radius:4px; font-size:10px;">Draft</span>
                button above. A tailored application will be generated and emailed to you for review (usually 2-3 minutes).
            </p>
        </div>
        """}

        <!-- Footer -->
        <div style="padding:18px 28px; background:#f8f9fa; border-radius:0 0 10px 10px; text-align:center;">
            <p style="margin:0; font-size:11px; color:{BRAND_GREY};">
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


def send_email_with_retry(html_body: str, recipients: list[str],
                          subject: str, from_email: str,
                          max_retries: int = 3) -> None:
    """Send email with retry logic for transient failures."""
    for attempt in range(1, max_retries + 1):
        try:
            send_email(html_body, recipients, subject, from_email)
            return
        except RuntimeError as e:
            if attempt < max_retries:
                wait = 30 * attempt
                print(f"  Send attempt {attempt}/{max_retries} failed: {e}")
                print(f"  Retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(f"  All {max_retries} send attempts failed.")
                raise


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"=== Weekly Contract Report — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    search_cfg, email_cfg = load_configs()

    # 1. Fetch and filter contracts
    results = fetch_and_filter(search_cfg, email_cfg)
    print(f"\n{len(results)} contracts after filtering")

    # 2. Load tracking and merge new results
    tracking = load_tracking()
    tracking = merge_results(tracking, results)
    new_ids = get_new_contract_ids(tracking)
    pipeline = get_pipeline_contracts(tracking)
    print(f"  {len(new_ids)} new contracts, {sum(len(v) for v in pipeline.values())} in pipeline")

    # 3. Score contracts for fit (non-critical — email still sends without scores)
    fit_scores = {}
    try:
        fit_scores = score_fit(results)
        for ocid, score in fit_scores.items():
            if ocid in tracking:
                tracking[ocid]["fit_score"] = score
    except Exception as e:
        print(f"  Fit scoring failed (non-critical): {e}")

    # 4. Build QC flags (non-critical)
    qc_flags = {}
    try:
        qc_flags = build_qc_flags(results)
    except Exception as e:
        print(f"  QC flags failed (non-critical): {e}")

    # 5. Build email
    date_range = datetime.now().strftime('%d %b %Y')

    if results:
        subject = f"{len(results)} Open AI & Data Gov Contracts Under \u00a3500k \u2014 {date_range}"
    else:
        subject = f"Weekly Gov Contracts \u2014 No New Opportunities \u2014 {date_range}"

    draft_trigger_url = email_cfg.get("draft_trigger_url", "").rstrip("/")

    html = build_email_html(
        results, date_range,
        new_ids=new_ids,
        pipeline=pipeline,
        fit_scores=fit_scores,
        qc_flags=qc_flags,
        draft_trigger_url=draft_trigger_url,
    )

    # 6. Send email
    from_email = email_cfg.get("from_email", "marta@inferencegroup.com")
    recipients = email_cfg["recipients"]
    send_email_with_retry(html, recipients, subject, from_email)

    # 7. Post-send: update tracking and save snapshot
    mark_seen(tracking)
    save_tracking(tracking)
    snapshot_path = save_weekly_snapshot(results)
    print(f"  Tracking saved. Snapshot: {snapshot_path.name}")

    print("Done.")


if __name__ == "__main__":
    main()
