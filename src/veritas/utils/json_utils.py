"""JSON utilities for Veritas."""

import json
from pathlib import Path
from typing import Any, Optional


def load_json(path: Path) -> dict:
    """Load JSON from file."""
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def save_json(data: dict, path: Path, indent: int = 2):
    """Save dict to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=indent)


def merge_results(results: list[dict]) -> dict:
    """
    Merge multiple evaluation results into a single dict.

    Args:
        results: List of evaluation result dicts

    Returns:
        Merged dict with all results
    """
    merged = {
        "Checklist": {},
        "Rationale": {},
        "Metrics": {},
    }

    for result in results:
        if "Checklist" in result:
            merged["Checklist"].update(result["Checklist"])
        if "Rationale" in result:
            merged["Rationale"].update(result["Rationale"])
        if "Metrics" in result:
            merged["Metrics"].update(result["Metrics"])

    return merged


def calculate_score(checklist: dict) -> tuple[int, int, float]:
    """
    Calculate pass/fail score from checklist.

    Args:
        checklist: Dict of check_id -> "PASS"/"FAIL"/"NA"

    Returns:
        Tuple of (passed, total, percentage)
    """
    passed = 0
    total = 0

    for value in checklist.values():
        if value == "NA":
            continue
        total += 1
        if value == "PASS":
            passed += 1

    percentage = (passed / total * 100) if total > 0 else 0
    return passed, total, percentage
