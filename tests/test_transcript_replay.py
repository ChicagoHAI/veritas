"""Transcript replay: rebuild the action sequence and file states from the log alone."""

import json

from veritas.utils.transcript_replay import extract_actions, replay, UNKNOWN


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


def _numbered(*lines):
    """A Read result in the CLI's `cat -n` format."""
    return "\n".join(f"{i:>6}\t{ln}" for i, ln in enumerate(lines, 1))


def test_read_recovers_content_for_a_later_edit_without_seed(tmp_path):
    # The file pre-existed, so there is no Write to replay -- but the agent
    # Read it before editing, and that result carries the prior content.
    path = "/workspace/output/replication/codebase/pre.py"
    events = [
        _tool_use("t1", "Read", {"file_path": path}),
        _tool_result("t1", _numbered("a = 1", "b = 2")),
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
        _tool_result("t1", _numbered("x = 1", "", "y = 2")),
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
        _tool_result("t2", _numbered("exact")),
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
