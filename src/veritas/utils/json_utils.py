"""JSON utilities for Veritas."""

import json
import re
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


def extract_json_from_text(text: str) -> Optional[dict]:
    """
    Extract JSON object from text that may contain other content.

    Tries three strategies in order:
    1. Markdown code block extraction (```json ... ```)
    2. Balanced-brace counting to find {"Checklist": ...}
    3. Raw json.loads() on the entire text

    Args:
        text: Text that may contain a JSON object

    Returns:
        Parsed JSON dict or None if not found
    """
    # Strategy 1: Extract from markdown code block
    md_match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', text)
    if md_match:
        try:
            return json.loads(md_match.group(1))
        except json.JSONDecodeError:
            pass

    # Strategy 2: Balanced-brace extraction looking for Checklist key
    result = _extract_balanced_json(text)
    if result is not None:
        return result

    # Strategy 3: Try entire text as JSON
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    return None


def _extract_balanced_json(text: str) -> Optional[dict]:
    """
    Find a JSON object containing "Checklist" using balanced brace counting.

    Properly handles nested braces and string literals.

    Args:
        text: Text that may contain a JSON object

    Returns:
        Parsed JSON dict or None if not found
    """
    start = text.find('{')
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\' and in_string:
                escape = True
                continue
            if c == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict) and "Checklist" in data:
                            return data
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find('{', start + 1)
    return None


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
