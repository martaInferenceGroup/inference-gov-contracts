"""
Rule-based Fit Scoring
=======================
Scores each contract 1-5 based on how well it matches Inference Group's
capabilities, ideal value range, and buyer sector.

Calibrated to: Inference Group (TCN Capital) — ~14 person AI/data
consultancy, strong in strategy/discovery/engineering, G-Cloud/DOS
experience in team, sweet spot £25k-£250k, public sector focus.

Config-driven: weights and signals are in config/fit_scoring.json.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = ROOT / "config" / "fit_scoring.json"


def load_fit_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _match_any(patterns: list[str], text: str) -> bool:
    """Check if any pattern matches in text (case-insensitive, word boundary)."""
    for p in patterns:
        if re.search(r"\b" + re.escape(p.lower()) + r"\b", text):
            return True
    return False


def score_contract(contract: dict, config: dict | None = None) -> int:
    """Score a single contract for fit (1-5).

    Returns an integer 1 (poor fit) to 5 (excellent fit).
    """
    if config is None:
        config = load_fit_config()

    title = (contract.get("title") or "").lower()
    desc = (contract.get("description") or "").lower()
    buyer = (contract.get("buyer") or "").lower()
    value = contract.get("total_value")

    weights = config["weights"]
    raw_score = 0.0

    # Strong signals — core Inference Group capabilities
    for signal in config["strong_signals"]:
        pattern = re.escape(signal.lower())
        if re.search(r"\b" + pattern + r"\b", title):
            raw_score += weights["strong_signal_title"]
        if re.search(r"\b" + pattern + r"\b", desc):
            raw_score += weights["strong_signal_desc"]

    # Moderate signals — adjacent capabilities
    for signal in config["moderate_signals"]:
        pattern = re.escape(signal.lower())
        if re.search(r"\b" + pattern + r"\b", title):
            raw_score += weights["moderate_signal_title"]
        if re.search(r"\b" + pattern + r"\b", desc):
            raw_score += weights["moderate_signal_desc"]

    # Weak signals — poor fit indicators
    for signal in config["weak_signals"]:
        pattern = re.escape(signal.lower())
        if re.search(r"\b" + pattern + r"\b", title):
            raw_score += weights["weak_signal_penalty"]
            break  # One penalty is enough

    # Value range fit
    ideal = config.get("ideal_value_range", {})
    if value is not None and value > 0:
        ideal_min = ideal.get("min", 0)
        ideal_max = ideal.get("max", float("inf"))
        stretch_max = ideal.get("stretch_max", ideal_max)

        if ideal_min <= value <= ideal_max:
            raw_score += weights["value_in_range_bonus"]
        elif ideal_max < value <= stretch_max:
            raw_score += weights.get("value_stretch_penalty", -0.25)
        else:
            raw_score += weights["value_out_of_range_penalty"]

    # Buyer bonus — sectors where Inference Group has experience/credibility
    for term in config.get("buyer_bonus", []):
        if term.lower() in buyer:
            raw_score += weights.get("buyer_bonus", 0.5)
            break

    # Buyer penalty — sectors requiring clearance or where IG has no presence
    for term in config.get("buyer_penalty", []):
        if term.lower() in buyer:
            raw_score += weights.get("buyer_penalty", -1.0)
            break

    # Cap the contribution from description matches to prevent
    # long descriptions with many keyword hits from inflating scores
    raw_score = min(raw_score, 10.0)

    # Normalise to 1-5 scale
    # Raw score typically ranges from -3 to ~10
    # Map: <=0 -> 1, 0.1-2 -> 2, 2.1-4 -> 3, 4.1-6 -> 4, >6 -> 5
    if raw_score <= 0:
        return 1
    elif raw_score <= 2:
        return 2
    elif raw_score <= 4:
        return 3
    elif raw_score <= 6:
        return 4
    else:
        return 5


def score_all(contracts: list[dict], config: dict | None = None) -> dict[str, int]:
    """Score all contracts and return a dict of ocid -> score."""
    if config is None:
        config = load_fit_config()

    return {
        c.get("ocid", ""): score_contract(c, config)
        for c in contracts
        if c.get("ocid")
    }
