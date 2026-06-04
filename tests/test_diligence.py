"""Unit tests for objective execution facts (`veritas.core.diligence`).

These cover the FACTS the module asserts — step counts, missing planned steps,
exit-code detection, declared-output-file presence, byte-identical repeated
commands, and effort accounting — on synthetic evidence and the real on-disk
`replication_log.json`. There are deliberately NO keyword/pattern tests: the
module no longer does any semantic (placeholder/skip/downsize) matching. Those
judgments belong to the manager (an LLM); deterministic code asserts only facts.

The module must also never raise on malformed or missing evidence.
"""

import json
from pathlib import Path

import pytest

from veritas.core.diligence import (
    ExecutionFacts,
    compute_execution_facts,
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
          stderr="", output_files=None, notes="", fixes=None, duration=0.0):
    return StepOutcome(
        step_id=step_id,
        description=description,
        command_executed=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_files=list(output_files or []),
        duration_seconds=duration,
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


# --- step coverage (planned vs executed; missing = set difference) ----------


def test_all_planned_steps_executed():
    facts = compute_execution_facts(_evidence([_step(1), _step(2), _step(3)]), _plan([1, 2, 3]))
    assert facts.planned_steps == 3
    assert facts.executed_steps == 3
    assert facts.missing_step_ids == []


def test_missing_planned_step_detected():
    # step 2 produced no record
    facts = compute_execution_facts(_evidence([_step(1), _step(3)]), _plan([1, 2, 3]))
    assert facts.planned_steps == 3
    assert facts.executed_steps == 2
    assert facts.missing_step_ids == [2]


def test_no_plan_falls_back_to_executed_count():
    facts = compute_execution_facts(_evidence([_step(1), _step(2)]), None)
    assert facts.planned_steps == 2
    assert facts.missing_step_ids == []


def test_extra_executed_steps_not_counted_as_missing():
    # executed more steps than planned: no planned step is missing
    facts = compute_execution_facts(_evidence([_step(1), _step(2), _step(3)]), _plan([1, 2]))
    assert facts.planned_steps == 2
    assert facts.executed_steps == 3
    assert facts.missing_step_ids == []


# --- exit codes (nonzero == failure, a fact) --------------------------------


def test_exit_codes_recorded_per_step():
    facts = compute_execution_facts(
        _evidence([_step(1, exit_code=0), _step(2, exit_code=2)]), None
    )
    assert facts.exit_codes == {1: 0, 2: 2}
    assert facts.succeeded_steps == 1
    assert facts.failed_steps == 1
    assert facts.failed_step_ids == [2]


def test_last_step_failed_flag():
    ok = compute_execution_facts(_evidence([_step(1, exit_code=1), _step(2, exit_code=0)]), None)
    assert ok.last_step_failed is False
    bad = compute_execution_facts(_evidence([_step(1, exit_code=0), _step(2, exit_code=1)]), None)
    assert bad.last_step_failed is True


def test_all_steps_succeed_no_failures():
    facts = compute_execution_facts(_evidence([_step(1), _step(2)]), None)
    assert facts.failed_steps == 0
    assert facts.failed_step_ids == []
    assert facts.last_step_failed is False


# --- declared output files (present/absent is a fact) -----------------------


def test_declared_output_files_present_and_absent():
    facts = compute_execution_facts(
        _evidence([
            _step(1, output_files=["results.csv", "fig.png"]),
            _step(2, output_files=[]),
        ]),
        None,
    )
    assert facts.steps_with_output_files == [1]
    assert facts.steps_without_output_files == [2]
    assert facts.total_output_files == 2


# --- stuck / looping (byte-identical commands; string equality only) --------


def test_repeated_identical_commands_counted():
    facts = compute_execution_facts(
        _evidence([
            _step(1, command="python train.py"),
            _step(2, command="python train.py"),
            _step(3, command="python  train.py"),  # whitespace-collapsed => identical
        ]),
        None,
    )
    assert facts.max_command_repeat == 3
    assert facts.repeated_commands["python train.py"] == 3


def test_distinct_commands_not_flagged_as_repeats():
    facts = compute_execution_facts(
        _evidence([_step(1, command="python a.py"), _step(2, command="python b.py")]),
        None,
    )
    assert facts.max_command_repeat == 1
    assert facts.repeated_commands == {}


def test_blank_commands_ignored_for_repeat():
    facts = compute_execution_facts(_evidence([_step(1, command=""), _step(2, command="")]), None)
    assert facts.max_command_repeat == 1
    assert facts.repeated_commands == {}


# --- effort accounting ------------------------------------------------------


def test_fix_and_duration_counts():
    fix = AppliedFix(file_path="a.py", description="patch", original_error="e", diff_snippet="d")
    facts = compute_execution_facts(
        _evidence([
            _step(1, fixes=[fix, fix], duration=1.5),
            _step(2, fixes=[fix], duration=2.0),
        ]),
        None,
    )
    assert facts.total_fixes_applied == 3
    assert facts.total_duration_seconds == pytest.approx(3.5)


# --- no-evidence / robustness (never raises) --------------------------------


def test_no_evidence_marks_flag_and_surfaces_planned_missing():
    facts = compute_execution_facts(None, _plan([1, 2, 3]))
    assert facts.no_evidence is True
    assert facts.planned_steps == 3
    assert facts.missing_step_ids == [1, 2, 3]
    assert facts.executed_steps == 0


def test_empty_evidence_marks_no_evidence():
    facts = compute_execution_facts(_evidence([]), None)
    assert facts.no_evidence is True
    assert facts.executed_steps == 0


def test_no_evidence_no_plan_is_safe():
    facts = compute_execution_facts(None, None)
    assert facts.no_evidence is True
    assert facts.planned_steps == 0
    assert facts.missing_step_ids == []


def test_never_raises_on_malformed_steps():
    # A step with a None command / odd output_files should not blow up.
    bad = StepOutcome(step_id=1, description="", command_executed=None, exit_code=0,
                      output_files=None)  # type: ignore[arg-type]
    facts = compute_execution_facts(_evidence([bad]), None)
    assert facts.executed_steps == 1
    # output_files=None coerces to empty
    assert facts.steps_without_output_files == [1]
    json.dumps(facts.to_dict())


def test_to_dict_is_json_serializable_and_has_expected_keys():
    facts = compute_execution_facts(_evidence([_step(1, output_files=["a"])]), _plan([1]))
    d = facts.to_dict()
    for key in ("planned_steps", "executed_steps", "missing_step_ids",
                "succeeded_steps", "failed_steps", "failed_step_ids", "exit_codes",
                "last_step_failed", "steps_with_output_files",
                "steps_without_output_files", "total_output_files",
                "repeated_commands", "max_command_repeat", "total_fixes_applied",
                "total_duration_seconds", "no_evidence"):
        assert key in d
    json.dumps(d)


def test_summary_line_is_factual_no_verdict():
    facts = compute_execution_facts(_evidence([_step(1, output_files=["a"])]), _plan([1]))
    line = facts.summary_line()
    assert "steps=" in line
    # No diligence verdict language in the factual summary.
    assert "diligent" not in line.lower()


def test_summary_line_no_evidence():
    line = compute_execution_facts(None, None).summary_line()
    assert "no replication evidence" in line


# --- real on-disk example ---------------------------------------------------

REAL_LOG = Path(
    "/data/haokunliu/veritas-workspace/results/smoke-html/cb-3849634/"
    "replication/replication_log.json"
)


@pytest.mark.skipif(not REAL_LOG.exists(), reason="real replication_log.json not present")
def test_real_replication_log_facts():
    data = json.loads(REAL_LOG.read_text(encoding="utf-8"))
    ev = ExecutionEvidence.from_dict(data)
    facts = compute_execution_facts(ev, plan=None)
    assert isinstance(facts, ExecutionFacts)
    # This run (cb-3849634) ran all 4 steps and produced artifacts.
    assert facts.no_evidence is False
    assert facts.executed_steps == ev.steps_attempted == 4
    # All steps succeeded => no failures, last step did not fail.
    assert facts.failed_steps == 0
    assert facts.failed_step_ids == []
    assert facts.last_step_failed is False
    # At least one step declared output files (18 PNGs etc.).
    assert facts.total_output_files >= 1
    assert facts.steps_with_output_files
    # No identical command repeated 3+ times in this clean run.
    assert facts.max_command_repeat < 3
    # Output is JSON-serializable.
    json.dumps(facts.to_dict())


@pytest.mark.skipif(not REAL_LOG.exists(), reason="real replication_log.json not present")
def test_real_replication_log_exit_codes_all_zero():
    data = json.loads(REAL_LOG.read_text(encoding="utf-8"))
    ev = ExecutionEvidence.from_dict(data)
    facts = compute_execution_facts(ev, plan=None)
    assert all(code == 0 for code in facts.exit_codes.values())
    assert facts.succeeded_steps == facts.executed_steps
