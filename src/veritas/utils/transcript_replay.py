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
from typing import Any, Dict, Iterator, List, Optional, Tuple

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
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def extract_actions(transcript_path: Path) -> List[Action]:
    """Flatten a claude stream-json transcript into ordered tool actions.

    Order is the order of ``tool_use`` blocks in the event stream; array
    order within one assistant message preserves issue order for parallel
    calls. Results are attached by ``tool_use_id``.
    """
    actions: List[Action] = []
    by_id: Dict[str, Action] = {}
    for event in iter_events(transcript_path):
        msg = event.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        if event.get("type") == "assistant":
            for block in content:
                if block.get("type") == "tool_use":
                    action = Action(
                        seq=len(actions) + 1,
                        tool=block.get("name", "?"),
                        input=block.get("input", {}),
                    )
                    actions.append(action)
                    by_id[block.get("id", "")] = action
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
    for prefix in STRIP_PREFIXES:
        if path_str.startswith(prefix):
            return path_str[len(prefix):]
    return path_str


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
                key = str(p.relative_to(seed_dir))
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
            # A successful Read of an unknown file recovers its content.
            if not a.is_error and a.result_text and vfs.get(path) is UNKNOWN:
                vfs[path] = a.result_text
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
    if res.unknown_paths:
        report += ["", "## Files whose content could not be recovered", ""]
        report += [f"  {p}" for p in res.unknown_paths]
    (out_dir / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
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
        f"  |  opaque commands: {len(res.opaque)}  |  broken edits: {len(res.broken)}"
    )
    print(f"Report: {args.out_dir / 'report.md'}")
    print(f"Tree:   {args.out_dir / 'reconstructed'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
