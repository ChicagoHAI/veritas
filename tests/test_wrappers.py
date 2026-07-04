"""Static invariants on the bash wrappers that bash -n cannot catch."""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_BACKSLASH = chr(92)


def test_eval_strings_carry_no_literal_backslash_n():
    # A literal backslash-n inside an eval'd docker command line is
    # syntactically valid bash but injects a stray token at run time
    # (docker once parsed one as the image name). Continuations must be
    # real backslash-newline.
    pattern = re.compile(re.escape(_BACKSLASH + "n") + "[ \t]+[$]")
    for wrapper in ("docker/run.sh", "veritas-host"):
        text = (REPO_ROOT / wrapper).read_text(encoding="utf-8")
        match = pattern.search(text)
        assert match is None, (
            f"literal backslash-n token in {wrapper} at offset {match.start()}"
        )
