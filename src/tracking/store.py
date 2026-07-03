"""
Contract Tracking Store
========================
Persists contract status across weekly runs so we can track:
- New vs previously seen contracts
- Application pipeline (shortlisted -> drafting -> submitted -> won/lost)
- History of status changes

Storage: data/tracking/contracts.json (committed to repo)
"""

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
TRACKING_FILE = ROOT / "data" / "tracking" / "contracts.json"
SNAPSHOTS_DIR = ROOT / "data" / "processed"

# Valid statuses in lifecycle order
STATUSES = ("new", "seen", "shortlisted", "drafting", "draft_sent",
            "submitted", "won", "lost", "skipped")

# Statuses that mean "actively in progress"
ACTIVE_STATUSES = ("shortlisted", "drafting", "draft_sent", "submitted")

# Statuses that mean "terminal" — don't auto-update
TERMINAL_STATUSES = ("won", "lost", "skipped")


def _normalise_date(raw: str) -> str:
    """Normalise a date to YYYY-MM-DD for comparison. Returns '' if unparseable."""
    if not raw or not raw.strip() or raw.startswith("0001"):
        return ""
    s = raw.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    s = re.sub(r",?\s*\d{1,2}[:.]\d{2}\s*(?:am|pm|AM|PM)?$", "", s)
    s = re.sub(r"(\d+)(?:st|nd|rd|th)\b", r"\1", s)
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            continue
    return ""


def load_tracking() -> dict:
    """Load the tracking file. Returns empty dict if missing or corrupt."""
    if not TRACKING_FILE.exists():
        return {}
    try:
        with open(TRACKING_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_tracking(data: dict) -> None:
    """Write the tracking file."""
    TRACKING_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKING_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def merge_results(tracking: dict, results: list[dict], today: str | None = None) -> dict:
    """Merge freshly scraped results into the tracking store.

    - New contracts get status 'new'
    - Previously seen contracts get last_seen updated
    - Contracts in terminal statuses are not modified
    - Expired contracts (closing_date < today) with status new/seen get auto-skipped

    Returns the updated tracking dict.
    """
    today = today or datetime.now().strftime("%Y-%m-%d")

    for r in results:
        ocid = r.get("ocid", "")
        if not ocid:
            continue

        if ocid in tracking:
            entry = tracking[ocid]
            entry["last_seen"] = today

            # Update fields that might have changed (deadline, value, etc.)
            if entry["status"] not in TERMINAL_STATUSES:
                for field in ("closing_date", "total_value", "title", "buyer", "link"):
                    if r.get(field):
                        entry[field] = r[field]
        else:
            # New contract
            tracking[ocid] = {
                "ocid": ocid,
                "title": r.get("title", ""),
                "buyer": r.get("buyer", ""),
                "source": r.get("source", ""),
                "closing_date": r.get("closing_date", ""),
                "total_value": r.get("total_value"),
                "link": r.get("link", ""),
                "fit_score": None,
                "status": "new",
                "first_seen": today,
                "last_seen": today,
                "status_history": [
                    {"status": "new", "date": today, "by": "system"}
                ],
                "draft_id": None,
                "notes": "",
            }

    # Auto-skip expired contracts that haven't been acted on
    for ocid, entry in tracking.items():
        closing = _normalise_date(entry.get("closing_date", ""))
        if (closing and closing < today
                and entry["status"] in ("new", "seen")):
            _update_status(entry, "skipped", today, "system",
                           reason="deadline passed")

    return tracking


def update_contract_status(tracking: dict, ocid: str, new_status: str,
                           by: str, reason: str = "",
                           today: str | None = None) -> bool:
    """Update a single contract's status.

    Returns True if the update was applied, False if the contract wasn't
    found or the status transition is invalid.
    """
    if new_status not in STATUSES:
        return False

    entry = tracking.get(ocid)
    if not entry:
        return False

    today = today or datetime.now().strftime("%Y-%m-%d")
    _update_status(entry, new_status, today, by, reason)
    return True


def _update_status(entry: dict, new_status: str, date: str, by: str,
                   reason: str = "") -> None:
    """Internal: set status and append to history.

    Includes idempotency guard: won't add duplicate history entries if the
    same status+date+by combination already exists.
    """
    entry["status"] = new_status
    history_entry = {"status": new_status, "date": date, "by": by}
    if reason:
        history_entry["reason"] = reason

    # Idempotency: don't add duplicate history entries
    existing = entry.setdefault("status_history", [])
    for h in existing:
        if (h.get("status") == new_status and h.get("date") == date
                and h.get("by") == by):
            return
    existing.append(history_entry)


def get_pipeline_contracts(tracking: dict) -> dict[str, list[dict]]:
    """Get contracts grouped by active status for the pipeline section.

    Returns dict like:
        {"drafting": [...], "draft_sent": [...], "submitted": [...], "won": [...]}
    """
    pipeline: dict[str, list[dict]] = {}
    for entry in tracking.values():
        status = entry.get("status", "")
        if status in ACTIVE_STATUSES or status == "won":
            pipeline.setdefault(status, []).append(entry)

    # Sort each group by closing date
    for group in pipeline.values():
        group.sort(key=lambda e: e.get("closing_date", "") or "9999-99-99")

    return pipeline


def get_new_contract_ids(tracking: dict) -> set[str]:
    """Return OCIDs of contracts with status 'new' (first seen this run)."""
    return {ocid for ocid, entry in tracking.items()
            if entry.get("status") == "new"}


def mark_seen(tracking: dict, today: str | None = None) -> None:
    """After the email is sent, move 'new' contracts to 'seen'."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    for entry in tracking.values():
        if entry["status"] == "new":
            _update_status(entry, "seen", today, "system",
                           reason="included in weekly email")


# -------------------------------------------------------------------------
# Weekly snapshots
# -------------------------------------------------------------------------

def save_weekly_snapshot(results: list[dict], date: str | None = None) -> Path:
    """Save this week's emailed results as a numbered snapshot.

    Returns the path to the snapshot file.
    """
    date = date or datetime.now().strftime("%Y-%m-%d")
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOTS_DIR / f"weekly_{date}.json"

    snapshot = []
    for i, r in enumerate(results, 1):
        entry = {**r, "email_number": i}
        snapshot.append(entry)

    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)

    _cleanup_old_snapshots(keep=8)
    return path


def load_latest_snapshot() -> list[dict] | None:
    """Load the most recent weekly snapshot. Returns None if none exist."""
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snapshots = sorted(SNAPSHOTS_DIR.glob("weekly_*.json"), reverse=True)
    if not snapshots:
        return None
    try:
        with open(snapshots[0]) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def resolve_contract_number(number: int, snapshot: list[dict] | None = None) -> dict | None:
    """Given a contract number (#1, #2, etc.) from the email, return the contract data."""
    if snapshot is None:
        snapshot = load_latest_snapshot()
    if not snapshot:
        return None
    for entry in snapshot:
        if entry.get("email_number") == number:
            return entry
    return None


def _cleanup_old_snapshots(keep: int = 8) -> None:
    """Delete old weekly snapshots, keeping the most recent `keep`."""
    snapshots = sorted(SNAPSHOTS_DIR.glob("weekly_*.json"), reverse=True)
    for old in snapshots[keep:]:
        old.unlink(missing_ok=True)
