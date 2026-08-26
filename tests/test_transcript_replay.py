"""Transcript replay: rebuild the action sequence and file states from the log alone."""

import builtins
import json

import pytest

from veritas.utils import transcript_replay
from veritas.utils.transcript_replay import (
    extract_actions,
    replay,
    write_outputs,
    UNKNOWN,
)


def _transcript(tmp_path, events):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return p


def _tool_use(tool_id, name, tool_input):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}}


def _tool_result(tool_id, text, is_error=False):
    block = {"type": "tool_result", "tool_use_id": tool_id,
             "content": [{"type": "text", "text": text}]}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def test_write_then_edit_reconstructs_final_content(tmp_path):
    path = "/workspace/output/replication/codebase/analyze.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "x = 1\ny = 2\n"}),
        _tool_result("t1", "File created"),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "y = 2", "new_string": "y = 3"}),
        _tool_result("t2", "Edit applied"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert res.reconstructed["analyze.py"] == "x = 1\ny = 3\n"
    assert not res.broken


def test_upto_materializes_intermediate_state(tmp_path):
    path = "/workspace/output/replication/codebase/analyze.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "v1\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "v1", "new_string": "v2"}),
        _tool_result("t2", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert replay(actions, upto=1).reconstructed["analyze.py"] == "v1\n"
    assert replay(actions, upto=2).reconstructed["analyze.py"] == "v2\n"


def test_out_of_codebase_paths_are_replayed_too(tmp_path):
    # The memory-file case: writes outside the codebase go through the same
    # logged tool calls and must be reconstructable identically.
    path = "/workspace/output/replication/replication_log.json"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "{}\n"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert replay(actions).reconstructed[path] == "{}\n"


def test_mutating_bash_is_flagged_opaque(tmp_path):
    events = [
        _tool_use("t1", "Bash", {"command": "python3 main.py > results/results.json"}),
        _tool_result("t1", "done"),
        _tool_use("t2", "Bash", {"command": "git status"}),
        _tool_result("t2", "clean"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert [seq for seq, _ in res.opaque] == [1]


def test_edit_without_prior_content_is_reported_broken(tmp_path):
    events = [
        _tool_use("t1", "Edit", {"file_path": "/workspace/output/replication/codebase/pre.py",
                                 "old_string": "a", "new_string": "b"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert res.vfs["pre.py"] is UNKNOWN
    assert res.broken and res.broken[0][1] == "pre.py"


def test_seed_dir_supplies_prior_content_for_edits(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "pre.py").write_text("a = 1\n", encoding="utf-8")
    events = [
        _tool_use("t1", "Edit", {"file_path": "/workspace/output/replication/codebase/pre.py",
                                 "old_string": "a = 1", "new_string": "a = 2"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions, seed_dir=seed)
    assert res.reconstructed["pre.py"] == "a = 2\n"
    assert not res.broken


def _init():
    """A session-boundary line: veritas transcripts hold several sessions."""
    return {"type": "system", "subtype": "init"}


def _numbered(content):
    """A Read result in the CLI's `cat -n` format, built the way the tool does.

    The tool numbers ``content.split("\\n")``, so a newline-terminated file
    emits a *final empty numbered entry* -- that entry is how the format
    expresses the trailing newline. A fixture that omits it lets a
    de-numbering bug and the tests agree with each other, which is what hid
    the appended-newline bug for three review rounds. Pass whole file content
    here, trailing newline included or not, exactly as it is on disk.
    """
    return "\n".join(
        f"{i:>6}\t{ln}" for i, ln in enumerate(content.split("\n"), 1)
    )


def test_read_recovers_content_for_a_later_edit_without_seed(tmp_path):
    # The file pre-existed, so there is no Write to replay -- but the agent
    # Read it before editing, and that result carries the prior content.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("a = 1\nb = 2\n")),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "b = 2", "new_string": "b = 3"}),
        _tool_result("t2", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert res.reconstructed["pre.py"] == "a = 1\nb = 3\n"
    assert not res.broken
    assert "pre.py" in res.approximate


def test_read_content_is_denumbered(tmp_path):
    # Storing the raw result would keep the "     1\t" prefixes, corrupting
    # the materialized file and breaking every subsequent old_string match.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("x = 1\n\ny = 2\n")),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert replay(actions).reconstructed["pre.py"] == "x = 1\n\ny = 2\n"


def test_read_of_non_file_output_leaves_path_unrecovered(tmp_path):
    # An empty-file notice or system reminder is prose, not content. Storing
    # it would be worse than admitting the content is unknown.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", "<system-reminder>File exists but is empty</system-reminder>"),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "a", "new_string": "b"}),
        _tool_result("t2", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert res.vfs["pre.py"] is UNKNOWN
    assert res.broken
    assert not res.approximate


def test_partial_read_is_not_treated_as_whole_file(tmp_path):
    # An offset Read numbers from the offset, so its text is a slice of the
    # file. Accepting it would silently truncate the reconstruction.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path, "offset": 40}),
        _tool_result("t1", "    40\tlate = 1\n    41\tlater = 2"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert "pre.py" not in res.reconstructed


def test_read_does_not_clobber_known_content(tmp_path):
    # A Read after a Write must not downgrade exact content to approximate.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "exact\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Read", {"file_path": path}),
        _tool_result("t2", _numbered("exact\n")),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions)
    assert res.reconstructed["a.py"] == "exact\n"
    assert not res.approximate


def test_nested_seed_entry_matches_transcript_path(tmp_path):
    # Seed keys are built from the filesystem, transcript keys from the log.
    # A nested path is where the two spellings can diverge (str() gives
    # backslashes on Windows); they must agree or the Edit looks broken.
    seed = tmp_path / "seed"
    (seed / "pkg").mkdir(parents=True)
    (seed / "pkg" / "mod.py").write_text("a = 1\n", encoding="utf-8")
    events = [
        _tool_use("t1", "Edit", {
            "file_path": "/workspace/output/replication/codebase/pkg/mod.py",
            "old_string": "a = 1", "new_string": "a = 2"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions, seed_dir=seed)
    assert res.reconstructed["pkg/mod.py"] == "a = 2\n"
    assert not res.broken


def test_backslash_transcript_paths_normalize(tmp_path):
    events = [
        _tool_use("t1", "Write", {
            "file_path": r"C:\runs\out\pkg\mod.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert "C:/runs/out/pkg/mod.py" in replay(actions).reconstructed


def test_duplicate_tool_use_id_across_sessions_replays_once(tmp_path):
    # A resumed session re-emits blocks the first one already carried. Counting
    # the repeat would inflate the sequence and re-apply the Edit to content
    # that already has it -- here, turning "v2" into a mismatch.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _init(),
        _tool_use("t1", "Write", {"file_path": path, "content": "v1\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "v1", "new_string": "v2"}),
        _tool_result("t2", "ok"),
        _init(),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "v1", "new_string": "v2"}),
        _tool_result("t2", "ok"),
        _tool_use("t3", "Edit", {"file_path": path,
                                 "old_string": "v2", "new_string": "v3"}),
        _tool_result("t3", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert [a.seq for a in actions] == [1, 2, 3]
    res = replay(actions)
    assert res.reconstructed["a.py"] == "v3\n"
    assert not res.broken


def test_malformed_and_non_dict_lines_are_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        "not json at all\n"
        '["a list, not an event"]\n'
        + json.dumps(_tool_use("t1", "Write", {
            "file_path": "/workspace/repo/a.py", "content": "x\n"})) + "\n"
        + json.dumps(_tool_result("t1", "ok")) + "\n",
        encoding="utf-8",
    )
    res = replay(extract_actions(p))
    assert res.reconstructed["a.py"] == "x\n"


def test_strip_prefix_handles_host_mode_paths(tmp_path):
    # Host-mode transcripts carry the host's own absolute paths, which no
    # built-in docker prefix matches; without stripping, the key stays
    # absolute and never lines up with a --seed tree.
    events = [
        _tool_use("t1", "Write", {
            "file_path": "/home/me/run/replication/codebase/pkg/mod.py",
            "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert "pkg/mod.py" in replay(
        actions, strip_prefixes=("/home/me/run/replication/codebase/",)
    ).reconstructed


def test_longest_strip_prefix_wins(tmp_path):
    # A caller who passes both a run root and its codebase subdirectory should
    # get the more specific match, regardless of argument order.
    events = [
        _tool_use("t1", "Write", {
            "file_path": "/run/replication/codebase/mod.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    res = replay(actions, strip_prefixes=("/run/", "/run/replication/codebase/"))
    assert "mod.py" in res.reconstructed


def test_failed_write_does_not_change_state(tmp_path):
    path = "/workspace/output/replication/codebase/analyze.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "good\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Write", {"file_path": path, "content": "bad\n"}),
        _tool_result("t2", "permission denied", is_error=True),
    ]
    actions = extract_actions(_transcript(tmp_path, events))
    assert replay(actions).reconstructed["analyze.py"] == "good\n"


def test_traversal_path_is_not_materialized_outside_the_tree(tmp_path):
    # VFS keys come from agent-authored tool input. write_outputs rmtree's its
    # destination first, so a key that resolves outside the tree would write
    # into an already-destructive context.
    events = [
        _tool_use("t1", "Write", {
            "file_path": "/workspace/repo/../../../escaped.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Write", {
            "file_path": "/workspace/repo/kept.py", "content": "y\n"}),
        _tool_result("t2", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    res = replay(extract_actions(transcript))
    out = tmp_path / "out"
    write_outputs(res, out, transcript, 2, None)

    assert (out / "reconstructed" / "kept.py").read_text() == "y\n"
    assert not list(tmp_path.parent.glob("escaped.py"))
    assert "escape the output tree" in (out / "report.md").read_text()


# -- out_dir is deleted recursively; guard it -------------------------------

def test_refuses_to_delete_unrelated_directory(tmp_path):
    victim = tmp_path / "important"
    victim.mkdir()
    (victim / "thesis.tex").write_text("years of work", encoding="utf-8")
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    res = replay(extract_actions(transcript))
    with pytest.raises(ValueError, match="refusing to delete"):
        write_outputs(res, victim, transcript, 1, None)
    assert (victim / "thesis.tex").exists()


def test_force_overwrites_unrelated_directory(tmp_path):
    victim = tmp_path / "scratch"
    victim.mkdir()
    (victim / "stale.txt").write_text("junk", encoding="utf-8")
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    res = replay(extract_actions(transcript))
    write_outputs(res, victim, transcript, 1, None, force=True)
    assert not (victim / "stale.txt").exists()
    assert (victim / "reconstructed" / "a.py").read_text() == "x\n"


def test_prior_replay_output_is_overwritten_without_force(tmp_path):
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    res = replay(extract_actions(transcript))
    out = tmp_path / "out"
    write_outputs(res, out, transcript, 1, None)
    write_outputs(res, out, transcript, 1, None)  # re-run must just work
    assert (out / "reconstructed" / "a.py").read_text() == "x\n"


def test_empty_out_dir_is_accepted(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py", "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    write_outputs(replay(extract_actions(transcript)), out, transcript, 1, None)
    assert (out / "reconstructed" / "a.py").exists()


# -- bounded / truncated Reads are not whole-file dumps ---------------------

def test_limited_read_is_not_treated_as_whole_file(tmp_path):
    # A limit-only Read numbers from 1, so the result text alone is
    # indistinguishable from a full dump. Accepting it would materialize a
    # truncated file and label it merely "approximate".
    path = "/workspace/output/replication/codebase/big.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path, "limit": 2}),
        # A bounded read is a prefix of the file, so there is no final empty
        # entry to mark a trailing newline -- it stops mid-file.
        _tool_result("t1", _numbered("line1\nline2")),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert "big.py" not in res.reconstructed
    assert not res.approximate


def test_truncated_read_notice_rejects_recovery(tmp_path):
    # The notice sits behind a blank line, so the check cannot look at only
    # the first non-numbered line.
    path = "/workspace/output/replication/codebase/big.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result(
            "t1",
            _numbered("l1\nl2")
            + "\n\n(Showing first 2 of 5000 lines. Use offset to read more.)",
        ),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert "big.py" not in res.reconstructed


def test_trailing_system_reminder_still_recovers(tmp_path):
    # Non-truncation trailing prose must not block recovery.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result(
            "t1",
            _numbered("x = 1\n") + "\n<system-reminder>note</system-reminder>",
        ),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.reconstructed["a.py"] == "x = 1\n"


# -- tool calls replay does not model ---------------------------------------

def test_unmodeled_tool_marks_its_file_unknown(tmp_path):
    # MultiEdit is not replayed. Leaving the pre-edit content in place would
    # produce a confidently wrong tree with nothing in the report to say so.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "v1\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "MultiEdit", {"file_path": path, "edits": []}),
        _tool_result("t2", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.vfs["a.py"] is UNKNOWN
    assert res.broken and res.broken[0][0] == 2
    assert res.unmodeled == [(2, "MultiEdit")]


def test_unmodeled_pathless_tool_is_still_reported(tmp_path):
    # A Task subagent's own tool calls never reach this transcript, so its
    # file effects are invisible; the report must still say it happened.
    events = [
        _tool_use("t1", "Task", {"prompt": "go fix the build"}),
        _tool_result("t1", "done"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.unmodeled == [(1, "Task")]
    assert not res.broken


def test_unmodeled_section_appears_in_report(tmp_path):
    events = [
        _tool_use("t1", "NotebookEdit",
                  {"notebook_path": "/workspace/repo/nb.ipynb", "new_source": "x"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    res = replay(extract_actions(transcript))
    out = tmp_path / "out"
    write_outputs(res, out, transcript, 1, None)
    assert "Unmodeled tool calls" in (out / "report.md").read_text()


def test_unreadable_seed_file_does_not_crash(tmp_path, monkeypatch):
    # chmod(0o000) does not revoke read access on Windows, so the OSError is
    # injected rather than produced by permissions -- the behavior under test
    # is the handler, not the OS's enforcement of file modes.
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "ok.py").write_text("a = 1\n", encoding="utf-8")
    (seed / "blocked.py").write_text("secret\n", encoding="utf-8")

    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file).endswith("blocked.py"):
            raise PermissionError("permission denied")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(transcript_replay, "open", fake_open, raising=False)
    res = replay([], seed_dir=seed)
    assert res.vfs["ok.py"] == "a = 1\n"
    assert res.vfs["blocked.py"] is UNKNOWN


@pytest.mark.parametrize(
    "content",
    ["a = 1\nb = 2\n", "a = 1\nb = 2", "one line\n", "one line", "", "\n"],
)
def test_read_recovery_round_trips_the_real_read_format(tmp_path, content):
    # The Read tool numbers content.split("\n"), so a newline-terminated file
    # emits a final empty numbered entry. Joining the recovered lines is exact
    # in both directions; appending a newline double-counts that entry.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered(content)),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.reconstructed["pre.py"] == content


# -- content the replay knows is wrong must not be presented as recovered ----

def test_mismatched_edit_marks_the_file_unknown(tmp_path):
    # The edit landed on the real file, so the bytes we hold have diverged
    # from it. Keeping them materializes a file we know is wrong while the
    # "could not be recovered" section stays empty.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "v1\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Edit", {"file_path": path,
                                 "old_string": "NOPE", "new_string": "v2"}),
        _tool_result("t2", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.vfs["a.py"] is UNKNOWN
    assert "a.py" not in res.reconstructed
    assert res.unknown_paths == ["a.py"]
    assert res.broken and res.broken[0][0] == 2


def test_edit_without_old_string_is_broken_not_a_prepend(tmp_path):
    # "" is never a legitimate anchor, but str.replace matches it at offset 0,
    # so the edit would silently prepend new_string and report no problem.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Write", {"file_path": path, "content": "v1\n"}),
        _tool_result("t1", "ok"),
        _tool_use("t2", "Edit", {"file_path": path, "new_string": "INJECTED"}),
        _tool_result("t2", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.vfs["a.py"] is UNKNOWN
    assert res.broken and "old_string" in res.broken[0][2]


def test_duplicate_tool_result_does_not_clobber_the_original(tmp_path):
    # A resumed session re-emits results as well as calls. The replayed copy
    # can be a shortened form of the same output; letting it win would lose
    # content the first result already recovered.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _init(),
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("a = 1\nb = 2\nc = 3\n")),
        _init(),
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("a = 1\n")),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.reconstructed["pre.py"] == "a = 1\nb = 2\nc = 3\n"


# -- byte-exactness and unusable inputs -------------------------------------

def test_missing_seed_dir_is_an_error(tmp_path):
    # rglob on a nonexistent path yields nothing, so a typo would silently
    # produce a fully-unreconstructable report that reads like a real finding
    # about the log rather than like a mistyped flag.
    with pytest.raises(ValueError, match="--seed"):
        replay([], seed_dir=tmp_path / "typo")


def test_seed_preserves_crlf_line_endings(tmp_path):
    # Universal-newline translation would rewrite CRLF to LF, so an Edit
    # anchored on a CRLF line would miss and report a spurious MISMATCH.
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "pre.py").write_bytes(b"a = 1\r\nb = 2\r\n")
    assert replay([], seed_dir=seed).vfs["pre.py"] == "a = 1\r\nb = 2\r\n"


def test_materialized_files_keep_their_own_line_endings(tmp_path):
    # write_text would translate "\n" to the platform separator, making every
    # file differ from the original under the docstring's `diff -r` check.
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py",
                                  "content": "x\ny\n"}),
        _tool_result("t1", "ok"),
    ]
    transcript = _transcript(tmp_path, events)
    out = tmp_path / "out"
    write_outputs(replay(extract_actions(transcript)), out, transcript, 1, None)
    assert (out / "reconstructed" / "a.py").read_bytes() == b"x\ny\n"


def test_write_after_read_clears_the_approximate_flag(tmp_path):
    # Once a Write supplies the exact bytes, the earlier inference no longer
    # applies -- leaving the flag set understates what is known exactly.
    path = "/workspace/output/replication/codebase/a.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("old = 1\n")),
        _tool_use("t2", "Write", {"file_path": path, "content": "new = 1\n"}),
        _tool_result("t2", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.reconstructed["a.py"] == "new = 1\n"
    assert not res.approximate


def test_non_dict_message_is_skipped(tmp_path):
    # The docstring invites other providers' transcripts; a stray string
    # message would otherwise raise AttributeError on .get.
    events = [
        {"type": "assistant", "message": "not a dict"},
        {"type": "user", "message": ["also not a dict"]},
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/a.py",
                                  "content": "x\n"}),
        _tool_result("t1", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert res.reconstructed["a.py"] == "x\n"


# -- opaque commands: script invocations and partial writes -----------------

@pytest.mark.parametrize(
    "cmd", ["bash setup.sh", "./run.sh", "sh build.sh", "../tools/gen.sh"]
)
def test_script_invocations_are_flagged_opaque(tmp_path, cmd):
    # Running a script hands the filesystem to code the log never shows.
    events = [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "ok")]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert [seq for seq, _ in res.opaque] == [1]


@pytest.mark.parametrize("cmd", ["git status", "ls -la", "echo hi", "ssh host ls"])
def test_read_only_commands_are_not_flagged_opaque(tmp_path, cmd):
    events = [_tool_use("t1", "Bash", {"command": cmd}), _tool_result("t1", "ok")]
    assert not replay(extract_actions(_transcript(tmp_path, events))).opaque


def test_failed_mutating_command_is_still_opaque(tmp_path):
    # A non-zero exit is not proof nothing was written: a script that failed
    # halfway still leaves whatever it wrote first.
    events = [
        _tool_use("t1", "Bash", {"command": "python3 gen.py > out.json"}),
        _tool_result("t1", "Traceback ...", is_error=True),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)))
    assert [seq for seq, _ in res.opaque] == [1]
    assert "exited non-zero" in res.opaque[0][1]


# -- seed-tree gaps are not log gaps ----------------------------------------

def test_unreadable_seed_files_are_not_reported_as_log_gaps(tmp_path):
    # A binary seed file is not something any transcript was going to carry,
    # so listing it under "could not be recovered" misreads the log's coverage.
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "logo.png").write_bytes(b"\x89PNG\x00\xff\xfe")
    (seed / "ok.py").write_text("a = 1\n", encoding="utf-8")
    res = replay([], seed_dir=seed)
    assert res.unknown_paths == []
    assert res.unreadable_seed_paths == ["logo.png"]


def test_git_directory_is_not_seeded(tmp_path):
    # A real repo copy carries thousands of binary objects; each would
    # otherwise be reported as content the log failed to cover.
    seed = tmp_path / "seed"
    (seed / ".git" / "objects").mkdir(parents=True)
    (seed / ".git" / "objects" / "ab12").write_bytes(b"\x00\xff")
    (seed / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (seed / "ok.py").write_text("a = 1\n", encoding="utf-8")
    res = replay([], seed_dir=seed)
    assert set(res.vfs) == {"ok.py"}


def test_write_clears_an_unreadable_seed_path(tmp_path):
    # Once the log supplies the bytes, the path is no longer a seed gap.
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "data.bin").write_bytes(b"\xff\xfe")
    events = [
        _tool_use("t1", "Write", {"file_path": "/workspace/repo/data.bin",
                                  "content": "now text\n"}),
        _tool_result("t1", "ok"),
    ]
    res = replay(extract_actions(_transcript(tmp_path, events)), seed_dir=seed)
    assert res.reconstructed["data.bin"] == "now text\n"
    assert res.unreadable_seed_paths == []


def test_seed_gap_section_appears_in_report(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "logo.png").write_bytes(b"\x89PNG\x00\xff\xfe")
    transcript = _transcript(tmp_path, [])
    out = tmp_path / "out"
    write_outputs(replay([], seed_dir=seed), out, transcript, 0, None)
    text = (out / "report.md").read_text()
    assert "Seed files whose bytes could not be read" in text
    assert "Files whose content could not be recovered" not in text
