"""Unit tests for deterministic diligence signals (`veritas.core.diligence`).

Covers each signal on synthetic evidence and the overall aggregation, plus a
real on-disk replication_log.json example.
"""

import json
from pathlib import Path

import pytest

from veritas.core.diligence import (
    DiligenceSignals,
    compute_artifacts,
    compute_diligence_signals,
    compute_downsizing,
    compute_placeholders,
    compute_premature_stop,
    compute_step_coverage,
    compute_stuck,
)
from veritas.core.models.replication import (
    AppliedFix,
    ExecutionEvidence,
    ReplicationPlan,
    ReplicationStep,
    StepOutcome,
)

# --- helpers ----------------------------------------------------------------


def _step(step_id, *, description="", command="cmd", exit_code=0, stdout="",
          stderr="", output_files=None, notes="", fixes=None):
    return StepOutcome(
        step_id=step_id,
        description=description,
        command_executed=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_files=list(output_files or []),
        notes=notes,
        fixes_applied=list(fixes or []),
    )


def _evidence(steps, environment=None):
    return ExecutionEvidence(environment=environment or {}, step_outcomes=list(steps))


def _plan(step_ids, expected="produce results.csv"):
    return ReplicationPlan(
        environment={},
        steps=[
            ReplicationStep(id=i, description=f"step {i}", command_hint="run",
                            expected_outcome=expected)
            for i in step_ids
        ],
    )


# --- step coverage ----------------------------------------------------------


def test_step_coverage_all_executed():
    ev = _evidence([_step(1), _step(2), _step(3)])
    plan = _plan([1, 2, 3])
    sig = compute_step_coverage(ev, plan)
    assert sig.planned_steps == 3
    assert sig.executed_steps == 3
    assert sig.missing_step_ids == []
    assert sig.all_planned_executed is True


def test_step_coverage_missing_step():
    ev = _evidence([_step(1), _step(3)])  # step 2 never ran
    plan = _plan([1, 2, 3])
    sig = compute_step_coverage(ev, plan)
    assert sig.missing_step_ids == [2]
    assert sig.all_planned_executed is False


def test_step_coverage_skipped_narration():
    ev = _evidence([
        _step(1),
        _step(2, notes="Skipped this step because data was missing; moved on."),
    ])
    plan = _plan([1, 2])
    sig = compute_step_coverage(ev, plan)
    assert 2 in sig.skipped_step_ids
    assert sig.all_planned_executed is False


def test_step_coverage_no_plan_falls_back_to_executed():
    ev = _evidence([_step(1), _step(2)])
    sig = compute_step_coverage(ev, None)
    assert sig.planned_steps == 2
    assert sig.missing_step_ids == []


# --- artifacts --------------------------------------------------------------


def test_artifacts_result_step_with_output_file():
    ev = _evidence([_step(1, description="generate the results table",
                          output_files=["results.csv"])])
    plan = _plan([1])
    sig = compute_artifacts(ev, plan)
    assert sig.result_steps_total == 1
    assert sig.result_steps_with_artifact == 1
    assert sig.all_result_steps_emitted is True


def test_artifacts_result_step_with_metric_in_stdout():
    ev = _evidence([_step(1, description="compute accuracy metric",
                          stdout="final accuracy = 0.834")])
    plan = _plan([1])
    sig = compute_artifacts(ev, plan)
    assert sig.result_steps_with_artifact == 1


def test_artifacts_result_step_missing_artifact():
    ev = _evidence([_step(1, description="generate the figure",
                          stdout="done", output_files=[])])
    plan = _plan([1], expected="save figure.png")
    sig = compute_artifacts(ev, plan)
    assert sig.result_steps_total == 1
    assert sig.result_steps_missing_artifact_ids == [1]
    assert sig.all_result_steps_emitted is False


def test_artifacts_non_result_step_ignored():
    ev = _evidence([_step(1, description="install dependencies", command="pip install foo")])
    plan = ReplicationPlan(environment={}, steps=[
        ReplicationStep(id=1, description="install deps", command_hint="pip install",
                        expected_outcome="environment ready"),
    ])
    sig = compute_artifacts(ev, plan)
    assert sig.result_steps_total == 0


# --- premature stop ---------------------------------------------------------


def test_premature_stop_unresolved_errors_thin_fixes():
    ev = _evidence([
        _step(1),
        _step(2, exit_code=1, stderr="Error: module not found", notes="gave up"),
    ])
    sig = compute_premature_stop(ev)
    assert sig.failed_steps == 1
    assert sig.failed_steps_with_unresolved_errors == 1
    assert sig.total_fixes_applied == 0
    assert sig.premature_stop_suspected is True


def test_premature_stop_not_flagged_when_fixes_applied():
    fix = AppliedFix(file_path="a.py", description="patch", original_error="ImportError",
                     diff_snippet="- x\n+ y")
    ev = _evidence([
        _step(1, fixes=[fix]),
        _step(2, fixes=[fix]),  # 2 fixes total, more than the failures
    ])
    sig = compute_premature_stop(ev)
    assert sig.premature_stop_suspected is False


def test_premature_stop_last_step_failed():
    ev = _evidence([
        _step(1, fixes=[AppliedFix("a", "b", "c", "d")]),
        _step(2, exit_code=2, stderr="Traceback: fatal error"),
    ])
    sig = compute_premature_stop(ev)
    assert sig.last_step_failed is True
    assert sig.premature_stop_suspected is True


# --- stuck / looping --------------------------------------------------------


def test_stuck_repeated_identical_commands():
    ev = _evidence([
        _step(1, command="python train.py"),
        _step(2, command="python train.py"),
        _step(3, command="python  train.py"),  # whitespace-different => normalized equal
    ])
    sig = compute_stuck(ev, repeat_threshold=3)
    assert sig.max_repeat == 3
    assert sig.stuck_suspected is True


def test_stuck_below_threshold():
    ev = _evidence([
        _step(1, command="python a.py"),
        _step(2, command="python a.py"),
        _step(3, command="python b.py"),
    ])
    sig = compute_stuck(ev, repeat_threshold=3)
    assert sig.max_repeat == 2
    assert sig.stuck_suspected is False
    assert "python a.py" in sig.repeated_commands


def test_stuck_ignores_blank_commands():
    ev = _evidence([_step(1, command=""), _step(2, command="")])
    sig = compute_stuck(ev)
    assert sig.max_repeat == 1
    assert sig.stuck_suspected is False


# --- downsizing -------------------------------------------------------------


def test_downsizing_detected_in_notes():
    ev = _evidence([
        _step(1, notes="Only ran 1 epoch to save time; full run is 100 epochs."),
    ])
    sig = compute_downsizing(ev)
    assert sig.downsizing_suspected is True
    assert 1 in sig.downsized_step_ids
    assert sig.hints[1]


def test_downsizing_toy_run():
    ev = _evidence([_step(1, notes="used a toy subset of the data")])
    sig = compute_downsizing(ev)
    assert sig.downsizing_suspected is True


def test_downsizing_clean_run():
    ev = _evidence([_step(1, notes="ran the full grid over all 100 epochs")])
    sig = compute_downsizing(ev)
    assert sig.downsizing_suspected is False


# --- placeholders / silent exceptions ---------------------------------------


def test_placeholder_keyword_in_notes():
    ev = _evidence([_step(1, notes="returned a placeholder value for now (TODO)")])
    sig = compute_placeholders(ev)
    assert sig.placeholder_suspected is True
    assert 1 in sig.flagged_step_ids


def test_placeholder_swallowed_exception_in_fix_diff():
    fix = AppliedFix(file_path="run.py", description="wrap in try", original_error="err",
                     diff_snippet="+ try:\n+     compute()\n+ except Exception: pass")
    ev = _evidence([_step(1, fixes=[fix])])
    sig = compute_placeholders(ev)
    assert sig.placeholder_suspected is True


def test_placeholder_in_codebase_diff_added_lines_only():
    diff = (
        "--- a/run.py\n+++ b/run.py\n"
        "@@ -1 +1,2 @@\n"
        "-real_value = compute()\n"
        "+real_value = 0.5  # hardcode value for now\n"
    )
    ev = _evidence([_step(1)])
    sig = compute_placeholders(ev, codebase_diff=diff)
    assert sig.placeholder_suspected is True
    assert sig.diff_hints


def test_placeholder_ignores_removed_lines_in_diff():
    # A placeholder appearing only on a removed ("-") line was in the upstream
    # repo and was removed by the agent — not introduced. Should not flag.
    diff = (
        "--- a/run.py\n+++ b/run.py\n"
        "@@ -1,2 +1 @@\n"
        "-value = 0  # placeholder\n"
        "+value = compute()\n"
    )
    ev = _evidence([_step(1)])
    sig = compute_placeholders(ev, codebase_diff=diff)
    assert sig.diff_hints == []
    assert sig.placeholder_suspected is False


def test_placeholder_clean_run():
    ev = _evidence([_step(1, notes="computed real result from data")])
    sig = compute_placeholders(ev, codebase_diff="+ real = compute()\n")
    assert sig.placeholder_suspected is False


# --- top-level aggregation --------------------------------------------------


def test_aggregate_clean_run_is_diligent():
    ev = _evidence([
        _step(1, description="install deps", command="pip install -e ."),
        _step(2, description="run experiment and save results.csv",
              command="python run.py", output_files=["results.csv"],
              stdout="accuracy = 0.91"),
    ])
    plan = ReplicationPlan(environment={}, steps=[
        ReplicationStep(id=1, description="install deps", command_hint="pip install -e .",
                        expected_outcome="environment ready"),
        ReplicationStep(id=2, description="run experiment", command_hint="python run.py",
                        expected_outcome="save results.csv"),
    ])
    signals = compute_diligence_signals(ev, plan=plan)
    assert isinstance(signals, DiligenceSignals)
    assert signals.looks_diligent is True
    assert signals.hard_negative_reasons == []


def test_aggregate_missing_step_not_diligent():
    ev = _evidence([_step(1, output_files=["x"], description="produce x")])
    plan = _plan([1, 2, 3])
    signals = compute_diligence_signals(ev, plan=plan)
    assert signals.looks_diligent is False
    assert any("not executed" in r for r in signals.hard_negative_reasons)


def test_aggregate_no_evidence_not_diligent():
    signals = compute_diligence_signals(None)
    assert signals.looks_diligent is False
    assert signals.hard_negative_reasons
    signals2 = compute_diligence_signals(_evidence([]))
    assert signals2.looks_diligent is False


def test_aggregate_downsizing_is_advisory_not_hard_negative():
    # Downsizing alone should NOT flip looks_diligent; it's advisory.
    ev = _evidence([
        _step(1, description="run experiment, save metrics.json",
              output_files=["metrics.json"], stdout="acc 0.8",
              notes="downsized to 1 epoch due to time"),
    ])
    plan = _plan([1])
    signals = compute_diligence_signals(ev, plan=plan)
    assert signals.downsizing.downsizing_suspected is True
    assert signals.looks_diligent is True
    assert any("downsizing" in f for f in signals.advisory_flags)


def test_aggregate_to_dict_round_trips_keys():
    ev = _evidence([_step(1, output_files=["a"], description="produce a")])
    signals = compute_diligence_signals(ev, plan=_plan([1]))
    d = signals.to_dict()
    for key in ("looks_diligent", "hard_negative_reasons", "advisory_flags",
                "step_coverage", "artifacts", "premature_stop", "stuck",
                "downsizing", "placeholders"):
        assert key in d
    # JSON-serializable
    json.dumps(d)


def test_summary_line_mentions_verdict():
    ev = _evidence([_step(1, output_files=["a"], description="produce a")])
    signals = compute_diligence_signals(ev, plan=_plan([1]))
    line = signals.summary_line()
    assert "diligence=" in line
    assert "steps=" in line


# --- real on-disk example ---------------------------------------------------

REAL_LOG = Path(
    "/data/haokunliu/veritas-workspace/results/smoke-html/cb-3849634/"
    "replication/replication_log.json"
)


@pytest.mark.skipif(not REAL_LOG.exists(), reason="real replication_log.json not present")
def test_real_replication_log_is_diligent():
    data = json.loads(REAL_LOG.read_text(encoding="utf-8"))
    ev = ExecutionEvidence.from_dict(data)
    # This run (cb-3849634) ran all 4 steps, fixed path/library issues, and
    # produced 18 PNGs + an 18-block REML log: it should read as diligent.
    signals = compute_diligence_signals(ev, plan=None)
    assert ev.steps_attempted == 4
    assert signals.looks_diligent is True, signals.hard_negative_reasons
    # All steps succeeded => no premature stop, no missing artifacts.
    assert signals.premature_stop.premature_stop_suspected is False
    assert signals.artifacts.all_result_steps_emitted is True
    # No identical command repeated 3+ times.
    assert signals.stuck.stuck_suspected is False
    # Output is JSON-serializable.
    json.dumps(signals.to_dict())


@pytest.mark.skipif(not REAL_LOG.exists(), reason="real replication_log.json not present")
def test_real_replication_log_artifact_steps_detected():
    data = json.loads(REAL_LOG.read_text(encoding="utf-8"))
    ev = ExecutionEvidence.from_dict(data)
    signals = compute_diligence_signals(ev, plan=None)
    # Steps 3 (run meta-analysis) and 4 (confirm PNGs) are result-producing and
    # both list output files, so at least one artifact step should be detected
    # and all detected ones should have emitted.
    assert signals.artifacts.result_steps_total >= 1
    assert signals.artifacts.result_steps_with_artifact == signals.artifacts.result_steps_total
