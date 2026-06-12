"""Reads a JSONL transcript file and sums up the token counts from it."""
import json
from pathlib import Path


def sum_tokens_from_transcript(transcript_path: Path) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) summed across all assistant messages in a JSONL transcript."""
    input_tokens = 0
    output_tokens = 0
    if not transcript_path.exists():
        return input_tokens, output_tokens
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            usage = (
                event.get("message", {}).get("usage")
                or event.get("usage")
            )
            if usage:
                input_tokens += usage.get("input_tokens", 0)
                output_tokens += usage.get("output_tokens", 0)
    return input_tokens, output_tokens