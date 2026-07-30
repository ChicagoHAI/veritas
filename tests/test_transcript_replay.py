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
