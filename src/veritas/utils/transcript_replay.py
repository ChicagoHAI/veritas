"""Reconstruct the agent's action sequence and file states from a provider
transcript (the raw stream-json JSONL veritas saves for each phase).

This is the executable form of the observation-package completeness test:
"can the whole run be rebuilt from the log alone?" It answers two questions:

  1. SEQUENCE — the exact ordered list of actions (reads, edits, writes,
     commands), i.e. the sequence of writing code.
  2. STATE — the content of every agent-edited file after any action N
     (``--upto N``), including files outside the codebase (e.g. memory or
     evidence files), since those flow through the same logged tool calls.

It also reports what it provably CANNOT rebuild: files created by *executed
commands* (a script the agent ran writing ``results.json``) exist in the log
only as the command line, not as bytes. Such commands are flagged OPAQUE, and
any final file not accounted for by a Write/Edit traces back to one of them.

Currently parses the claude CLI stream-json format (``type: assistant`` events
carrying ``tool_use`` blocks; ``type: user`` events carrying ``tool_result``).
Codex/gemini transcripts need their own adapters.

Usage:
    python -m veritas.utils.transcript_replay TRANSCRIPT.jsonl OUT_DIR \
        [--seed DIR] [--upto N]

    --seed DIR   initial file tree (e.g. a reconstructed codegen tree, or the
                 pre-run repo copy); edits to pre-existing files can only be
                 replayed when their prior content is known.
    --upto N     materialize state as of action N instead of the end.

Validation against a real run: reconstruct codegen, then replicate seeded on
it, then diff against the run's final codebase::

    python -m veritas.utils.transcript_replay \
        <run>/replication/codegen_transcript.jsonl /tmp/rc
    python -m veritas.utils.transcript_replay \
        <run>/replication/replication_transcript.jsonl /tmp/rf \
        --seed /tmp/rc/reconstructed
    diff -r /tmp/rf/reconstructed <run>/replication/codebase
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

# Commands that plausibly mutate the filesystem -> flagged opaque. Heuristic
# by design: over-flagging costs a false warning, under-flagging hides a gap.
MUTATING_BASH = re.compile(
    r"(>>?|\btee\b|\bcp\b|\bmv\b|\brm\b|\bmkdir\b|\btouch\b|\bsed\s+-i"
    r"|\bpip3?\s+install|\buv\s+(pip|sync|add|venv)"
    r"|\bgit\s+(apply|am|checkout|reset)"
    r"|\bpython3?\s+|\bRscript\b|\bmake\b|\bcurl\b|\bwget\b|\btar\b|\bunzip\b)"
)

# Container path prefixes stripped so replayed paths line up with a --seed
# tree and with the on-host copy of the codebase.
STRIP_PREFIXES = (
    "/workspace/output/replication/codebase/",
    "/workspace/repo/",
)


class _Unknown:
    """Sentinel: file demonstrably exists but its bytes never appear in the log."""

    def __repr__(self) -> str:  # pragma: no cover
        return "<unknown content>"


UNKNOWN = _Unknown()

# A Read result line: `cat -n` numbering, then a tab, then the file's line.
_READ_LINE = re.compile(r"^\s*(\d+)\t(.*)$")


def _denumber_read(text: str) -> Optional[str]:
    """Recover file content from a `cat -n`-formatted Read result.

    Returns None when the result isn't a whole-file dump — an empty-file
    notice, a bare system reminder, an image, or a partial read (an
    ``offset`` starts the numbering above 1) — so the caller leaves the
    path UNKNOWN rather than storing prose as if it were content.

    Content recovered this way is *approximate*: `cat -n` cannot distinguish
    a file ending in a newline from one that doesn't, and a trailing newline
    is assumed. Callers should record the path in ``ReplayResult.approximate``.
    """
    out: List[str] = []
    first_no: Optional[int] = None
    for line in text.split("\n"):
        m = _READ_LINE.match(line)
        if m:
            if first_no is None:
                first_no = int(m.group(1))
            out.append(m.group(2))
        elif out:
            break  # trailing reminder / truncation notice — content ended
        else:
            return None  # never looked like a file dump
    if first_no != 1:
        return None  # partial read: this is not the whole file
    return "\n".join(out) + "\n"


@dataclass
class Action:
    """One tool call, in stream order."""

    seq: int
    tool: str
    input: Dict[str, Any]
    result_text: Optional[str] = None
    is_error: bool = False


@dataclass
class ReplayResult:
    vfs: Dict[str, Any]                       # path -> content str | UNKNOWN
    sequence: List[str] = field(default_factory=list)
    opaque: List[Tuple[int, str]] = field(default_factory=list)
    broken: List[Tuple[int, str, str]] = field(default_factory=list)
    # Paths whose content was recovered from a Read result rather than from a
    # Write. Byte-identity is not guaranteed for these: `cat -n` output cannot
    # express whether the file ended in a newline. Kept separate so the report
    # never claims exact reconstruction for content it only inferred.
    approximate: Set[str] = field(default_factory=set)

    @property
    def reconstructed(self) -> Dict[str, str]:
        return {p: c for p, c in self.vfs.items() if c is not UNKNOWN}

    @property
    def unknown_paths(self) -> List[str]:
        return sorted(p for p, c in self.vfs.items() if c is UNKNOWN)


def iter_events(path: Path) -> Iterator[Dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, RecursionError):
                # JSONDecodeError is a ValueError, but json.loads also raises
                # bare ValueError (>4300-digit ints, Py3.11+) and RecursionError
                # (deep nesting); a malformed line just gets skipped.
                continue
            if isinstance(obj, dict):
                yield obj


def extract_actions(transcript_path: Path) -> List[Action]:
    """Flatten a claude stream-json transcript into ordered tool actions.

    Order is the order of ``tool_use`` blocks in the event stream; array
    order within one assistant message preserves issue order for parallel
    calls. Results are attached by ``tool_use_id``.

    One transcript file legitimately holds several appended provider
    invocations (a JSON-repair re-prompt, a heartbeat session resume), each
    opening with a ``system``/``init`` line and each capable of re-emitting
    ``tool_use`` blocks the earlier session already carried. A block id is
    honored once: a duplicate would take a fresh sequence number, inflating
    the action count and replaying its Edit a second time against content
    that already has it applied.
    """
    actions: List[Action] = []
    by_id: Dict[str, Action] = {}
    seen_ids: set[str] = set()
    for event in iter_events(transcript_path):
        msg = event.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if event.get("type") == "assistant":
            for block in content:
                if block.get("type") == "tool_use":
                    block_id = block.get("id") or ""
                    if block_id and block_id in seen_ids:
                        continue
                    action = Action(
                        seq=len(actions) + 1,
                        tool=block.get("name", "?"),
                        input=block.get("input", {}),
                    )
                    actions.append(action)
                    # Index only real ids: keying id-less blocks on "" made
                    # them share one slot, cross-attaching their results.
                    if block_id:
                        seen_ids.add(block_id)
                        by_id[block_id] = action
        elif event.get("type") == "user":
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                action = by_id.get(block.get("tool_use_id", ""))
                if action is None:
                    continue
                rc = block.get("content")
                if isinstance(rc, list):
                    texts = [c.get("text", "") for c in rc if isinstance(c, dict)]
                    action.result_text = "\n".join(t for t in texts if t)
                elif isinstance(rc, str):
                    action.result_text = rc
                action.is_error = bool(block.get("is_error"))
    return actions


def _norm(path_str: str) -> str:
    # Transcript paths are posix in docker mode but can carry backslashes when
    # the agent ran on a Windows host. Normalize before matching so prefixes
    # still strip, and so VFS keys never disagree with the posix keys built
    # from a --seed tree (or materialize as one filename containing "\").
    p = path_str.replace("\\", "/")
    for prefix in STRIP_PREFIXES:
        if p.startswith(prefix):
            return p[len(prefix):]
    return p


def _apply_edit(content: str, old: str, new: str, replace_all: bool) -> Optional[str]:
    if old not in content:
        return None
    return content.replace(old, new) if replace_all else content.replace(old, new, 1)


def replay(
    actions: List[Action],
    seed_dir: Optional[Path] = None,
    upto: Optional[int] = None,
) -> ReplayResult:
    """Replay file-editing actions into a virtual file tree."""
    vfs: Dict[str, Any] = {}
    if seed_dir:
        for p in Path(seed_dir).rglob("*"):
            if p.is_file():
                # as_posix(), not str(): on Windows str() yields backslash
                # keys that never match _norm's forward-slash transcript keys,
                # so every seeded Edit would look unreconstructable.
                key = p.relative_to(seed_dir).as_posix()
                try:
                    vfs[key] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    vfs[key] = UNKNOWN

    res = ReplayResult(vfs=vfs)

    for a in actions:
        if upto is not None and a.seq > upto:
            break
        status = "ERR" if a.is_error else "ok"

        if a.tool == "Write":
            path = _norm(a.input.get("file_path", "?"))
            body = a.input.get("content", "")
            if not a.is_error:
                vfs[path] = body
            res.sequence.append(
                f"{a.seq:>4}. [{status}] Write  {path}  ({len(body.splitlines())} lines)"
            )
        elif a.tool == "Edit":
            path = _norm(a.input.get("file_path", "?"))
            cur = vfs.get(path)
            if a.is_error:
                res.sequence.append(
                    f"{a.seq:>4}. [ERR] Edit   {path}  (edit failed, no state change)"
                )
            elif cur is None or cur is UNKNOWN:
                res.broken.append((
                    a.seq, path,
                    "no known prior content (file pre-existed without --seed, "
                    "or was created by a command)",
                ))
                vfs[path] = UNKNOWN
                res.sequence.append(
                    f"{a.seq:>4}. [{status}] Edit   {path}  "
                    "(UNRECONSTRUCTABLE: prior content unknown)"
                )
            else:
                new = _apply_edit(
                    cur,
                    a.input.get("old_string", ""),
                    a.input.get("new_string", ""),
                    a.input.get("replace_all", False),
                )
                if new is None:
                    res.broken.append(
                        (a.seq, path, "old_string not found in reconstructed content")
                    )
                    res.sequence.append(
                        f"{a.seq:>4}. [{status}] Edit   {path}  (MISMATCH: old_string not found)"
                    )
                else:
                    vfs[path] = new
                    res.sequence.append(f"{a.seq:>4}. [{status}] Edit   {path}")
        elif a.tool == "Bash":
            cmd = a.input.get("command", "")
            first = cmd.splitlines()[0][:90] if cmd else "?"
            mutating = bool(MUTATING_BASH.search(cmd))
            if mutating and not a.is_error:
                res.opaque.append((a.seq, first))
            tag = "Bash*" if mutating else "Bash "
            res.sequence.append(f"{a.seq:>4}. [{status}] {tag}  {first}")
        elif a.tool == "Read":
            path = _norm(a.input.get("file_path", "?"))
            res.sequence.append(f"{a.seq:>4}. [{status}] Read   {path}")
            # A successful Read recovers the content of a file we don't have
            # bytes for -- either never seen (None: pre-existed without --seed)
            # or seen but unrecoverable (UNKNOWN). Both cases matter: recovering
            # here is what lets a later Edit on that file replay.
            cur = vfs.get(path)
            if (
                not a.is_error
                and a.result_text is not None
                and (cur is None or cur is UNKNOWN)
            ):
                recovered = _denumber_read(a.result_text)
                if recovered is not None:
                    vfs[path] = recovered
                    res.approximate.add(path)
        else:
            res.sequence.append(f"{a.seq:>4}. [{status}] {a.tool}")

    return res


def write_outputs(res: ReplayResult, out_dir: Path, transcript: Path,
                  n_actions: int, upto: Optional[int]) -> None:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    tree = out_dir / "reconstructed"
    tree.mkdir(parents=True)

    for path, content in sorted(res.reconstructed.items()):
        dest = tree / path.lstrip("/")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")

    upto_note = f" (state as of action {upto})" if upto else ""
    report: List[str] = [
        f"# Replay report{upto_note}", "",
        f"Transcript: {transcript}",
        f"Actions found: {n_actions}", "",
        "## Action sequence", "",
    ]
    report += res.sequence
    report += ["", "## Opaque commands (may have written files NOT captured in the log)", ""]
    report += [f"  action {seq}: {cmd}" for seq, cmd in res.opaque] or ["  (none)"]
    report += ["", "## Unreconstructable edits", ""]
    report += [f"  action {seq}: {path} — {why}" for seq, path, why in res.broken] or ["  (none)"]
    if res.approximate:
        report += [
            "", "## Files recovered from Read output (approximate)", "",
            "  Content came from a Read result, not a logged Write. The `cat -n`",
            "  format cannot express whether the file ended in a newline, so one",
            "  is assumed; these may differ from the original by that byte.", "",
        ]
        report += [f"  {p}" for p in sorted(res.approximate)]
    if res.unknown_paths:
        report += ["", "## Files whose content could not be recovered", ""]
        report += [f"  {p}" for p in res.unknown_paths]
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    # The opening sentence wraps across lines; collapse the whole first
    # paragraph so --help doesn't print a truncated fragment of it.
    ap = argparse.ArgumentParser(
        description=" ".join(__doc__.split("\n\n")[0].split())
    )
    ap.add_argument("transcript", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--seed", type=Path, default=None)
    ap.add_argument("--upto", type=int, default=None)
    args = ap.parse_args(argv)

    actions = extract_actions(args.transcript)
    res = replay(actions, args.seed, args.upto)
    write_outputs(res, args.out_dir, args.transcript, len(actions), args.upto)

    print(
        f"Actions: {len(actions)}  |  reconstructed files: {len(res.reconstructed)}"
        f" ({len(res.approximate)} approximate)"
        f"  |  opaque commands: {len(res.opaque)}  |  broken edits: {len(res.broken)}"
    )
    print(f"Report: {args.out_dir / 'report.md'}")
    print(f"Tree:   {args.out_dir / 'reconstructed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
