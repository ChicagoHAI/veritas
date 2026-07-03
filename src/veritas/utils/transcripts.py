"""Reads a JSONL transcript file and sums up the token counts from it."""
import json
from pathlib import Path


def sum_tokens_from_transcript(transcript_path: Path) -> tuple[int, int]:
    """Return (input_tokens, output_tokens) from a JSONL transcript.

    Input tokens: only the last usage event (avoids double-counting accumulated
    context across turns in a multi-turn agentic session).
    Output tokens: summed across all events (each turn's output is genuinely new).
    """
    last_input = 0
    total_output = 0
    if not transcript_path.exists():
        return 0, 0
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
                last_input = usage.get("input_tokens", last_input)
                total_output += usage.get("output_tokens", 0)
    return last_input, total_output