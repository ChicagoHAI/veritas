"""Integration tests for the runner's manager retry loop.

These drive ``ReplicationRunner._replicate_with_manager_loop`` directly with
stubbed phase methods, so they exercise the real loop control flow (archival,
state invalidation, guidance threading, workflow logging, termination, graceful
hand-off) without invoking any LLM or Docker. The deterministic helpers are
covered separately in ``test_manager.py``.
"""

from __future__ import annotations

import json

from veritas.core.config import Config
from veritas.core.diligence import DiligenceSignals
from veritas.core.manager import ManagerVerdict
from veritas.core.models.replication import (
    ExecutionEvidence,
    ReplicationPlan,
    ReplicationStep,
    StepOutcome,
)
from veritas.core.pipeline_state import PipelineState
from veritas.core.runner import ReplicationRunner


def _make_config(tmp_path, max_iters=3):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    out = tmp_path / "out"
    cfg = Config(repo_path=repo, output_dir=out, mode="repo-only", max_iters=max_iters)
    out.mkdir(parents=True, exist_ok=True)
    (out / "replication").mkdir(parents=True, exist_ok=True)
    (out / ".veritas").mkdir(parents=True, exist_ok=True)
    return cfg


def _evidence():
    return ExecutionEvidence(
        environment={},
        step_outcomes=[
            StepOutcome(step_id=1, description="run", command_executed="python x.py", exit_code=0)
        ],
    )


def _plan():
    return ReplicationPlan(
        environment={},
        steps=[ReplicationStep(id=1, description="run", command_hint="python x.py",
                               expected_outcome="results.csv")],
    )


def _neg_signals():
    s = DiligenceSignals()
    s.looks_diligent = False
    s.hard_negative_reasons = ["premature stop"]
    return s


def _clean_signals():
    s = DiligenceSignals()
    s.looks_diligent = True
    return s


def test_loop_off_single_pass(tmp_path, monkeypatch):
    """max_iters=1: replicate runs once, no manager review, no workflow log."""
    cfg = _make_config(tmp_path, max_iters=1)
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)

    calls = {"replicate": 0, "review": 0}

    def fake_replicate(plan, manager_guidance=None):
        calls["replicate"] += 1
        runner._last_signals = _clean_signals()
        return _evidence()

    def fake_review(*a, **k):
        calls["review"] += 1
        return ManagerVerdict(decision="accept")

    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    monkeypatch.setattr(runner, "_manager_review", fake_review)

    evidence, plan = runner._replicate_with_manager_loop(state, None, _plan())
    assert calls["replicate"] == 1
    assert calls["review"] == 0  # gate never runs when loop is off
    assert evidence is not None
    assert not cfg.workflow_log_path.exists()


def test_loop_accepts_iteration_1(tmp_path, monkeypatch):
    """Loop on, manager accepts immediately: one replicate, one review, logged."""
    cfg = _make_config(tmp_path, max_iters=3)
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)

    calls = {"replicate": 0}

    def fake_replicate(plan, manager_guidance=None):
        calls["replicate"] += 1
        runner._last_signals = _neg_signals()  # force the LLM gate path
        return _evidence()

    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    monkeypatch.setattr(
        runner, "_manager_review",
        lambda *a, **k: ManagerVerdict(decision="accept", reason="diligent enough"),
    )

    runner._replicate_with_manager_loop(state, None, _plan())
    assert calls["replicate"] == 1
    recs = [json.loads(ln) for ln in cfg.workflow_log_path.read_text().splitlines() if ln.strip()]
    reviews = [r for r in recs if r["phase"] == "manager_review"]
    assert len(reviews) == 1
    assert reviews[0]["manager_verdict"]["decision"] == "accept"


def test_loop_revise_then_accept_archives_and_injects_guidance(tmp_path, monkeypatch):
    """The headline re-run path: revise -> archive + invalidate + guidance -> accept."""
    cfg = _make_config(tmp_path, max_iters=3)
    # seed an artifact so archival has something to copy
    (cfg.replication_dir / "replication_log.json").write_text("{}", encoding="utf-8")
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)
    # mark a downstream phase completed so we can assert the re-run invalidates it
    state.start_stage("verify")
    state.complete_stage("verify", success=True)

    guidance_seen = []
    replicate_calls = {"n": 0}

    def fake_replicate(plan, manager_guidance=None):
        replicate_calls["n"] += 1
        guidance_seen.append(manager_guidance)
        # write fresh artifact each attempt (simulates a real re-run)
        (cfg.replication_dir / "replication_log.json").write_text(
            json.dumps({"attempt": replicate_calls["n"]}), encoding="utf-8"
        )
        runner._last_signals = _neg_signals()
        return _evidence()

    reviews = iter([
        ManagerVerdict(decision="revise", target_phase="replicate",
                       reason="step 1 downsized to a toy run",
                       directive="run step 1 at the full configured scale",
                       already_tried="ran step 1 with --max-samples 10",
                       deficiency_is_genuine="deficient"),
        ManagerVerdict(decision="accept", reason="now diligent"),
    ])
    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    monkeypatch.setattr(runner, "_manager_review", lambda *a, **k: next(reviews))

    runner._replicate_with_manager_loop(state, None, _plan())

    # two replicate runs: the initial + one re-run
    assert replicate_calls["n"] == 2
    # first replicate had no guidance; the re-run carried the manager's directive
    assert guidance_seen[0] is None
    assert guidance_seen[1] is not None
    assert "full configured scale" in guidance_seen[1].directive
    assert guidance_seen[1].iteration == 2

    # prior attempt was archived, not overwritten
    archive = cfg.replication_dir.parent / "replication.attempt-1"
    assert archive.exists()
    assert json.loads((archive / "replication_log.json").read_text()) == {"attempt": 1}

    # downstream verify state was invalidated by the re-run
    assert not state.is_stage_completed("verify")

    # workflow log records both reviews + the archived path
    recs = [json.loads(ln) for ln in cfg.workflow_log_path.read_text().splitlines() if ln.strip()]
    review_decisions = [r["manager_verdict"]["decision"] for r in recs if r["phase"] == "manager_review"]
    assert review_decisions == ["revise", "accept"]
    archived = [r for r in recs if r.get("archived_attempt_path")]
    assert archived and "attempt-1" in archived[0]["archived_attempt_path"]


def test_loop_cap_writes_handoff(tmp_path, monkeypatch):
    """Manager never accepts: loop stops at the cap with a graceful hand-off."""
    cfg = _make_config(tmp_path, max_iters=2)
    (cfg.replication_dir / "replication_log.json").write_text("{}", encoding="utf-8")
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)

    n = {"i": 0}

    def fake_replicate(plan, manager_guidance=None):
        n["i"] += 1
        (cfg.replication_dir / "replication_log.json").write_text(
            json.dumps({"a": n["i"]}), encoding="utf-8"
        )
        runner._last_signals = _neg_signals()
        return _evidence()

    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    # always revise, each time a DIFFERENT directive so the no-progress
    # terminator doesn't pre-empt the cap
    directives = iter([
        "approach A: rebuild the environment",
        "approach B: fetch the dataset from the mirror",
        "approach C: this should not be reached",
    ])
    monkeypatch.setattr(
        runner, "_manager_review",
        lambda *a, **k: ManagerVerdict(decision="revise", target_phase="replicate",
                                       reason="still failing", directive=next(directives),
                                       deficiency_is_genuine="deficient"),
    )

    runner._replicate_with_manager_loop(state, None, _plan())

    # capped at 2 iterations -> 2 replicate runs (initial + 1 re-run)
    assert n["i"] == 2
    handoff_recs = [
        json.loads(ln) for ln in cfg.workflow_log_path.read_text().splitlines()
        if ln.strip() and json.loads(ln).get("phase") == "handoff"
    ]
    assert handoff_recs, "expected a graceful hand-off record at the cap"
    assert handoff_recs[0]["handoff"]["stop_reason"] == "cap"
    md = (cfg.veritas_state_dir / "workflow.md").read_text(encoding="utf-8")
    assert "UNRESOLVED HAND-OFF" in md


def test_loop_resume_skips_when_already_converged(tmp_path, monkeypatch):
    """Resume: replicate completed + a prior accept in the log -> no re-review."""
    from veritas.core.manager import WorkflowLog
    cfg = _make_config(tmp_path, max_iters=3)
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)
    state.start_stage("replicate")
    state.complete_stage("replicate", success=True)
    wf = WorkflowLog(cfg.veritas_state_dir)
    wf.append({"iteration": 1, "phase": "replicate", "status": "completed"})
    wf.append({
        "iteration": 1, "phase": "manager_review", "status": "accept",
        "manager_verdict": {"decision": "accept", "source": "llm"},
    })
    (cfg.replication_dir / "replication_log.json").write_text(
        json.dumps({"step_outcomes": [], "environment": {}}), encoding="utf-8"
    )

    reviewed = {"n": 0}

    def fake_review(*a, **k):
        reviewed["n"] += 1
        return ManagerVerdict()

    monkeypatch.setattr(runner, "_manager_review", fake_review)
    runner._replicate_with_manager_loop(state, None, _plan())
    assert reviewed["n"] == 0  # converged loop is not re-entered on resume


def test_loop_no_progress_terminates_early(tmp_path, monkeypatch):
    """Same directive + no signal improvement -> stop before the cap."""
    cfg = _make_config(tmp_path, max_iters=5)
    (cfg.replication_dir / "replication_log.json").write_text("{}", encoding="utf-8")
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)

    n = {"i": 0}

    def fake_replicate(plan, manager_guidance=None):
        n["i"] += 1
        runner._last_signals = _neg_signals()  # never improves
        return _evidence()

    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    monkeypatch.setattr(
        runner, "_manager_review",
        lambda *a, **k: ManagerVerdict(decision="revise", target_phase="replicate",
                                       reason="same problem", directive="do the identical thing",
                                       deficiency_is_genuine="deficient"),
    )

    runner._replicate_with_manager_loop(state, None, _plan())

    # iter1 review -> revise -> rerun (i=2); iter2 review -> no-progress stop.
    # Stops well before the cap of 5.
    assert n["i"] == 2
    recs = [json.loads(ln) for ln in cfg.workflow_log_path.read_text().splitlines() if ln.strip()]
    handoffs = [r for r in recs if r.get("phase") == "handoff"]
    assert handoffs and handoffs[0]["handoff"]["stop_reason"] == "no-progress"
