"""Unit tests for early-termination persistence (`veritas.core.replication`).

The replicate heartbeat loop learns that a run was cut off at its time budget;
the agent's own `replication_log.json` cannot know, because the agent was
killed. `record_early_termination` stamps the marker into that log and
`gather_evidence` reads it back, so the fact survives into a later run that
re-reads evidence from disk rather than recomputing it in memory.

The regression these guard: an in-memory-only marker is silently dropped on a
resumed pipeline, and the recomputed execution facts then describe a
budget-truncated run as one that finished on its own.
"""

import json
from pathlib import Path

from veritas.core.diligence import compute_execution_facts
from veritas.core.replication import (
    REPLICATION_LOG_FILE,
    gather_evidence,
    record_early_termination,
)


def _write_log(replication_dir: Path, payload) -> Path:
    replication_dir.mkdir(parents=True, exist_ok=True)
    log_path = replication_dir / REPLICATION_LOG_FILE
    log_path.write_text(json.dumps(payload), encoding="utf-8")
    return log_path


AGENT_LOG = {
    "step_outcomes": [
        {
            "step_id": "step_1",
            "description": "Train the model",
            "command_executed": "python train.py",
            "exit_code": 0,
            "output_files": ["results/metrics.json"],
            "notes": "converged",
        }
    ]
}


def test_marker_round_trips_through_the_log(tmp_path):
    """record -> gather recovers both fields."""
    _write_log(tmp_path, AGENT_LOG)

    assert record_early_termination(tmp_path, "Reached the 3600s replicate budget after 3612s.")

    evidence = gather_evidence(tmp_path)
    assert evidence is not None
    assert evidence.terminated_early is True
    assert evidence.termination_reason == "Reached the 3600s replicate budget after 3612s."


def test_marker_survives_into_recomputed_facts(tmp_path):
    """The actual regression: a re-run re-reads the log and recomputes facts.

    Those facts must still report the cutoff, or the manager judges a truncated
    run as a lazy one.
    """
    _write_log(tmp_path, AGENT_LOG)
    record_early_termination(tmp_path, "Reached the 60s replicate budget after 62s.")

    # Simulates the resumed path: fresh read from disk, no in-memory carry-over.
    facts = compute_execution_facts(gather_evidence(tmp_path))
    assert facts.terminated_early is True
    assert facts.termination_reason == "Reached the 60s replicate budget after 62s."


def test_stamping_preserves_what_the_agent_wrote(tmp_path):
    """The marker merges into the log; it does not rewrite it."""
    _write_log(tmp_path, AGENT_LOG)
    record_early_termination(tmp_path, "budget")

    evidence = gather_evidence(tmp_path)
    assert evidence is not None
    assert evidence.steps_attempted == 1
    assert evidence.steps_succeeded == 1
    assert evidence.step_outcomes[0].step_id == "step_1"
    assert evidence.step_outcomes[0].output_files == ["results/metrics.json"]


def test_unstamped_log_reports_a_normal_finish(tmp_path):
    """A run that ended on its own must not look truncated."""
    _write_log(tmp_path, AGENT_LOG)

    evidence = gather_evidence(tmp_path)
    assert evidence is not None
    assert evidence.terminated_early is False
    assert evidence.termination_reason == ""


def test_missing_log_reports_failure_without_raising(tmp_path):
    """No agent log means no evidence to annotate — report it, don't crash.

    compute_execution_facts already flags the no-evidence case on its own.
    """
    assert record_early_termination(tmp_path, "budget") is False
    assert not (tmp_path / REPLICATION_LOG_FILE).exists()


def test_malformed_log_reports_failure_without_raising(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / REPLICATION_LOG_FILE).write_text("{not json", encoding="utf-8")

    assert record_early_termination(tmp_path, "budget") is False


def test_non_object_log_reports_failure_without_raising(tmp_path):
    """A JSON array parses fine but has nowhere to put the marker."""
    _write_log(tmp_path, [{"step_id": "step_1"}])

    assert record_early_termination(tmp_path, "budget") is False


def test_stamping_twice_keeps_the_latest_reason(tmp_path):
    """The manager loop re-runs replicate; the newest cutoff is the true one."""
    _write_log(tmp_path, AGENT_LOG)
    record_early_termination(tmp_path, "first cutoff")
    record_early_termination(tmp_path, "second cutoff")

    evidence = gather_evidence(tmp_path)
    assert evidence is not None
    assert evidence.termination_reason == "second cutoff"
