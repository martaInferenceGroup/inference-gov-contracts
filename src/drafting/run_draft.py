"""
Draft Application Runner
==========================
End-to-end pipeline: resolve a contract from the weekly email,
fetch full notice, extract requirements, draft responses, send email,
update tracking.

Run manually:
    python -m src.drafting.run_draft --contract 4
    python -m src.drafting.run_draft --contract 4 --week 2026-04-18

Triggered via: GitHub Actions (.github/workflows/draft-application.yml)

Requires environment variables:
    ANTHROPIC_API_KEY — for Claude drafting
    MS_CLIENT_ID, MS_TENANT_ID, MS_REFRESH_TOKEN — for email sending
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.scrapers.contracts_finder import fetch_full_notice
from src.analysis.requirement_extractor import extract_requirements
from src.drafting.drafter import draft_full_application
from src.drafting.send_draft import send_draft, build_draft_email
from src.tracking.store import (load_tracking, save_tracking,
                                update_contract_status,
                                load_latest_snapshot, resolve_contract_number)

ROOT = Path(__file__).parent.parent.parent
EMAIL_CONFIG = ROOT / "config" / "email_criteria.json"


def load_email_config() -> dict:
    with open(EMAIL_CONFIG) as f:
        return json.load(f)


def run(contract_number: int, week_date: str | None = None,
        test_mode: bool = False,
        template_type: str | None = None) -> None:
    """Run the full drafting pipeline for a contract.

    Args:
        contract_number: The #N from the weekly email (e.g. 4 for #4).
        week_date: Optional date string to load a specific week's snapshot.
                   If None, loads the latest snapshot.
        test_mode: If True, send only to marta@inferencegroup.com.
        template_type: Optional application type override (e.g. "restricted_sq").
                       If None, auto-detected from the notice.
    """
    print(f"=== Draft Application — Contract #{contract_number} ===")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    # 1. Resolve contract number to contract data
    snapshot = None
    if week_date:
        snapshot_path = ROOT / "data" / "processed" / f"weekly_{week_date}.json"
        if snapshot_path.exists():
            with open(snapshot_path) as f:
                snapshot = json.load(f)
        else:
            print(f"  Snapshot not found: {snapshot_path.name}")
            print(f"  Falling back to latest snapshot")

    contract = resolve_contract_number(contract_number, snapshot)
    if not contract:
        print(f"  ERROR: Contract #{contract_number} not found in snapshot")
        print(f"  Available numbers: check the latest weekly email")
        sys.exit(1)

    title = contract.get("title", "Unknown")
    ocid = contract.get("ocid", "")
    source = contract.get("source", "")
    print(f"  Contract: {title}")
    print(f"  Buyer: {contract.get('buyer', 'Unknown')}")
    print(f"  Source: {source}")
    print(f"  OCID: {ocid}")

    # 2. Update tracking status to "drafting"
    tracking = load_tracking()
    if ocid in tracking:
        current_status = tracking[ocid].get("status", "")
        if current_status in ("drafting", "draft_sent"):
            print(f"  WARNING: This contract is already in '{current_status}' status.")
            print(f"  Continuing anyway — will overwrite previous draft.")
        update_contract_status(tracking, ocid, "drafting",
                             by="system", reason="Draft pipeline started")
        save_tracking(tracking)

    # 3. Fetch full notice detail
    print(f"\n  Fetching full notice...")
    if source == "Contracts Finder":
        notice = fetch_full_notice(ocid)
        if not notice:
            print(f"  WARNING: Could not fetch full notice. Using search result data.")
            notice = contract
    else:
        # For Find a Tender, we don't have a detail API — use search data
        print(f"  Note: Full notice fetch only available for Contracts Finder.")
        print(f"  Using search result data for Find a Tender contract.")
        notice = contract

    # 4. Extract requirements
    print(f"  Extracting requirements...")
    requirements = extract_requirements(notice)

    print(f"  Found {len(requirements['sections'])} response sections:")
    for s in requirements["sections"]:
        conf = "high" if s["confidence"] >= 0.7 else "medium" if s["confidence"] >= 0.4 else "low"
        print(f"    - {s['section']} ({conf} confidence)")

    if requirements.get("criteria"):
        print(f"  Award criteria:")
        for c in requirements["criteria"]:
            w = f"{c['weight_pct']}%" if c.get("weight_pct") else "unspecified"
            print(f"    - {c['label']}: {w}")

    if requirements.get("documents_required"):
        print(f"  Documents to prepare: {', '.join(requirements['documents_required'][:5])}")

    # 5. Draft all sections
    print(f"\n  Drafting application...")
    application = draft_full_application(requirements, template_type=template_type)

    total_words = application["summary"]["total_words"]
    total_sections = application["summary"]["total_sections"]
    print(f"\n  Draft complete: {total_sections} sections, {total_words:,} words")

    # 6. Save HTML preview locally
    html = build_draft_email(application)
    preview_path = ROOT / "data" / f"draft_preview_{ocid[:20]}.html"
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  Preview saved: {preview_path.name}")

    # 7. Send email (skip if MS Graph credentials aren't available)
    import os
    if not os.environ.get("MS_CLIENT_ID"):
        print(f"  Skipping email send (MS Graph credentials not set).")
        print(f"  Open the preview file in a browser to review the draft.")
    else:
        email_cfg = load_email_config()
        if test_mode:
            recipients = ["marta@inferencegroup.com"]
            print(f"  TEST MODE: Sending to marta@inferencegroup.com only")
        else:
            recipients = email_cfg["recipients"]

        from_email = email_cfg.get("from_email", "marta@inferencegroup.com")

        print(f"  Sending draft to {', '.join(recipients)}...")
        send_draft(application, recipients, from_email)

    # 8. Update tracking
    tracking = load_tracking()  # Reload in case of concurrent updates
    status = "draft_sent" if os.environ.get("MS_CLIENT_ID") else "drafting"
    update_contract_status(tracking, ocid, status,
                         by="system", reason=f"Draft completed ({total_sections} sections)")
    save_tracking(tracking)

    print(f"\n  Done. Tracking updated to '{status}'.")


def main():
    parser = argparse.ArgumentParser(
        description="Draft a tender application for a contract from the weekly email."
    )
    parser.add_argument(
        "--contract", "-c", type=int, required=True,
        help="Contract number from the weekly email (e.g. 4 for #4)"
    )
    parser.add_argument(
        "--week", "-w", type=str, default=None,
        help="Week date to load snapshot from (e.g. 2026-04-18). Default: latest."
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Test mode: send draft to marta@inferencegroup.com only."
    )
    parser.add_argument(
        "--template", "-t", type=str, default=None,
        help="Application type override (e.g. open_procedure, restricted_sq, restricted_itt, below_threshold). Default: auto-detect."
    )

    args = parser.parse_args()
    run(contract_number=args.contract, week_date=args.week,
        test_mode=args.test, template_type=args.template)


if __name__ == "__main__":
    main()
