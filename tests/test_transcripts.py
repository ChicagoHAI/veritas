"""Token tallying from a provider transcript.

These pin the malformed-line guards. A JSONL transcript is whatever the
provider wrote, and this function is called during reporting on a run that
has already finished -- so one odd line must cost its own tokens, never the
whole tally.
"""

import json

from veritas.utils.transcripts import sum_tokens_from_transcript


def _write(tmp_path, *lines):
    p = tmp_path / "transcript.jsonl"
    p.write_text(
        "\n".join(ln if isinstance(ln, str) else json.dumps(ln) for ln in lines)
        + "\n",
        encoding="utf-8",
    )
    return p


def _usage(inp, out):
    return {"type": "assistant", "message": {
        "usage": {"input_tokens": inp, "output_tokens": out}}}


def test_missing_file_returns_zero(tmp_path):
    assert sum_tokens_from_transcript(tmp_path / "absent.jsonl") == (0, 0)


def test_last_input_wins_and_output_accumulates(tmp_path):
    # Input is the running context, so only the last event counts; output is
    # genuinely new each turn, so it sums.
    p = _write(tmp_path, _usage(100, 5), _usage(180, 7), _usage(240, 3))
    assert sum_tokens_from_transcript(p) == (240, 15)


def test_top_level_usage_is_also_counted(tmp_path):
    p = _write(tmp_path, {"usage": {"input_tokens": 12, "output_tokens": 4}})
    assert sum_tokens_from_transcript(p) == (12, 4)


def test_malformed_lines_do_not_abort_the_tally(tmp_path):
    # json.loads raises bare ValueError on a >4300-digit int (Py3.11+) and
    # RecursionError on deep nesting -- neither is a JSONDecodeError, so
    # catching only that subclass would let one line kill the whole count.
    # Both are written as raw text: constructing the oversized int in Python
    # would trip the same limit here in the test.
    p = _write(
        tmp_path,
        "not json at all",
        "[" * 10000 + "]" * 10000,          # RecursionError
        '{"n": ' + "1" * 5000 + "}",        # bare ValueError, not a subclass
        _usage(50, 9),
    )
    assert sum_tokens_from_transcript(p) == (50, 9)


def test_non_dict_and_odd_shaped_events_are_skipped(tmp_path):
    # Every level is type-checked: a bare list line, a string `message`, a
    # null `message`, and a non-dict `usage` each raised AttributeError.
    p = _write(
        tmp_path,
        ["a list, not an event"],
        '"just a string"',
        {"type": "assistant", "message": "not a dict"},
        {"type": "assistant", "message": None},
        {"type": "assistant", "message": {"usage": "not a dict"}},
        {"type": "assistant", "usage": ["also not a dict"]},
        _usage(70, 11),
    )
    assert sum_tokens_from_transcript(p) == (70, 11)


def test_blank_lines_are_ignored(tmp_path):
    p = _write(tmp_path, "", _usage(20, 2), "   ", _usage(30, 3), "")
    assert sum_tokens_from_transcript(p) == (30, 5)
