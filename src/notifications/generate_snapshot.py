"""
Generate Weekly Snapshot
=========================
Fetches contracts and saves a snapshot (+ updates tracking) without
sending the email. Useful for testing the drafting pipeline.

Run:  python -m src.notifications.generate_snapshot
"""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.notifications.weekly_report import load_configs, fetch_and_filter
from src.analysis.fit_scoring import score_all as score_fit
from src.tracking.store import (load_tracking, save_tracking, merge_results,
                                mark_seen, save_weekly_snapshot)


def main():
    print(f"=== Generate Snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")

    search_cfg, email_cfg = load_configs()

    results = fetch_and_filter(search_cfg, email_cfg)
    print(f"\n{len(results)} contracts after filtering")

    if not results:
        print("No results — nothing to snapshot.")
        return

    # Update tracking
    tracking = load_tracking()
    tracking = merge_results(tracking, results)

    # Score
    fit_scores = score_fit(results)
    for ocid, score in fit_scores.items():
        if ocid in tracking:
            tracking[ocid]["fit_score"] = score

    # Save
    mark_seen(tracking)
    save_tracking(tracking)
    snapshot_path = save_weekly_snapshot(results)

    print(f"\nSnapshot saved: {snapshot_path.name}")
    print(f"Tracking updated: {len(tracking)} contracts total")
    print(f"\nContracts available for drafting:")
    for i, r in enumerate(results, 1):
        title = r.get("title", "")[:55]
        buyer = r.get("buyer", "")[:25]
        score = fit_scores.get(r.get("ocid", ""), "?")
        print(f"  #{i:2d}  [{score}] {title:55s}  {buyer}")


if __name__ == "__main__":
    main()
