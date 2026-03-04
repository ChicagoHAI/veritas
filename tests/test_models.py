"""Tests for replication data models."""

import json
from veritas.core.models import (
    ReplicationStep, ReplicationPlan,
    StepOutcome, ExecutionEvidence,
)


class TestReplicationStep:
    def test_create(self):
        step = ReplicationStep(id=1, description="Install deps", command_hint="pip install", expected_outcome="OK")
        assert step.id == 1
        assert step.description == "Install deps"

    def test_to_dict(self):
        step = ReplicationStep(id=1, description="Run", command_hint="python run.py", expected_outcome="Done")
        d = step.to_dict()
        assert d["id"] == 1
        assert d["command_hint"] == "python run.py"

    def test_from_dict(self):
        data = {"id": 2, "description": "Test", "command_hint": "pytest", "expected_outcome": "Pass"}
        step = ReplicationStep.from_dict(data)
        assert step.id == 2
        assert step.expected_outcome == "Pass"


class TestReplicationPlan:
    def test_create(self):
        plan = ReplicationPlan(
            environment={"language": "python"},
            steps=[ReplicationStep(id=1, description="Run", command_hint="python", expected_outcome="OK")],
        )
        assert len(plan.steps) == 1
        assert plan.environment["language"] == "python"

    def test_to_dict_roundtrip(self):
        plan = ReplicationPlan(
            environment={"language": "python"},
            steps=[ReplicationStep(id=1, description="Run", command_hint="python", expected_outcome="OK")],
        )
        d = plan.to_dict()
        restored = ReplicationPlan.from_dict(d)
        assert len(restored.steps) == 1
        assert restored.steps[0].description == "Run"

    def test_from_json_string(self):
        raw = json.dumps({
            "environment": {"language": "python"},
            "steps": [{"id": 1, "description": "Run", "command_hint": "python", "expected_outcome": "OK"}],
        })
        plan = ReplicationPlan.from_json(raw)
        assert len(plan.steps) == 1


class TestStepOutcome:
    def test_create_success(self):
        outcome = StepOutcome(
            step_id=1, description="Run", command_executed="python run.py",
            exit_code=0, stdout="OK", stderr="", output_files=["out.txt"],
            duration_seconds=5.0, suggested_fix=None, code_modified=False, notes="",
        )
        assert outcome.succeeded is True

    def test_create_failure(self):
        outcome = StepOutcome(
            step_id=1, description="Run", command_executed="python run.py",
            exit_code=1, stdout="", stderr="Error", output_files=[],
            duration_seconds=2.0, suggested_fix="Fix path", code_modified=False, notes="",
        )
        assert outcome.succeeded is False
        assert outcome.suggested_fix == "Fix path"

    def test_to_dict(self):
        outcome = StepOutcome(
            step_id=1, description="Run", command_executed="python run.py",
            exit_code=0, stdout="OK", stderr="", output_files=[],
            duration_seconds=5.0, suggested_fix=None, code_modified=False, notes="",
        )
        d = outcome.to_dict()
        assert d["step_id"] == 1
        assert d["exit_code"] == 0


class TestExecutionEvidence:
    def test_create_and_summary(self):
        evidence = ExecutionEvidence(
            environment={"python_version": "3.12"},
            step_outcomes=[
                StepOutcome(step_id=1, description="Install", command_executed="pip install",
                            exit_code=0, stdout="OK", stderr="", output_files=[],
                            duration_seconds=10.0, suggested_fix=None, code_modified=False, notes=""),
                StepOutcome(step_id=2, description="Run", command_executed="python run.py",
                            exit_code=1, stdout="", stderr="Error", output_files=[],
                            duration_seconds=5.0, suggested_fix=None, code_modified=False, notes=""),
            ],
        )
        assert evidence.steps_attempted == 2
        assert evidence.steps_succeeded == 1
        assert evidence.steps_failed == 1
        assert evidence.total_duration_seconds == 15.0

    def test_to_dict_roundtrip(self):
        evidence = ExecutionEvidence(
            environment={"python_version": "3.12"},
            step_outcomes=[
                StepOutcome(step_id=1, description="Run", command_executed="python",
                            exit_code=0, stdout="OK", stderr="", output_files=["out.txt"],
                            duration_seconds=5.0, suggested_fix=None, code_modified=False, notes=""),
            ],
        )
        d = evidence.to_dict()
        restored = ExecutionEvidence.from_dict(d)
        assert restored.steps_attempted == 1
        assert restored.step_outcomes[0].description == "Run"

    def test_empty_evidence(self):
        evidence = ExecutionEvidence(environment={}, step_outcomes=[])
        assert evidence.steps_attempted == 0
        assert evidence.steps_succeeded == 0
        assert evidence.total_duration_seconds == 0.0

    def test_all_output_files(self):
        evidence = ExecutionEvidence(
            environment={},
            step_outcomes=[
                StepOutcome(step_id=1, description="Run", command_executed="python",
                            exit_code=0, stdout="", stderr="", output_files=["a.txt", "b.txt"],
                            duration_seconds=1.0, suggested_fix=None, code_modified=False, notes=""),
                StepOutcome(step_id=2, description="Eval", command_executed="python",
                            exit_code=0, stdout="", stderr="", output_files=["c.txt"],
                            duration_seconds=1.0, suggested_fix=None, code_modified=False, notes=""),
            ],
        )
        assert evidence.all_output_files == ["a.txt", "b.txt", "c.txt"]
