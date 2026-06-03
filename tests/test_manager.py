"""Unit tests for the manager-controlled retry loop (Phase 2).

Covers the deterministic, LLM-free pieces the design requires to be
trustworthy: verdict parsing/normalization, the termination predicate (accept /
cap / no-progress), the no-progress comparison over OBJECTIVE EXECUTION FACTS,
archival, the workflow-log writer, and the graceful hand-off.

There is no deterministic short-circuit-accept anymore: the manager (LLM) always
judges diligence. The facts here drive only the mechanical no-progress check.
"""

from __future__ import annotations

from veritas.core.diligence import ExecutionFacts
from veritas.core.manager import (
    DECISION_ACCEPT,
    DECISION_REVISE,
    GENUINENESS_DEFICIENT,
    GENUINENESS_DIVERGENT,
    GENUINENESS_IRREDUCIBLE,
    ManagerGuidance,
    ManagerVerdict,
    WorkflowLog,
    archive_attempt,
    build_handoff,
    facts_improved,
    parse_manager_verdict,
    should_stop,
    signals_improved,
)

# --- helpers ----------------------------------------------------------------


def _clean_facts() -> ExecutionFacts:
    f = ExecutionFacts()
    f.planned_steps = 2
    f.executed_steps = 2
    f.succeeded_steps = 2
    return f


def _negative_facts(missing_steps=None, failed_steps=1) -> ExecutionFacts:
    f = ExecutionFacts()
    f.missing_step_ids = missing_steps or []
    f.failed_steps = failed_steps
    f.failed_step_ids = list(range(1, failed_steps + 1))
    return f


# Back-compat aliases used by tests below.
_clean_signals = _clean_facts
_negative_signals = _negative_facts


# --- verdict parsing --------------------------------------------------------


def test_parse_accept_minimal():
    v = parse_manager_verdict({"decision": "accept"})
    assert v.accepted
    assert v.decision == DECISION_ACCEPT
    assert v.source == "llm"


def test_parse_revise_with_directive_and_target():
    v = parse_manager_verdict({
        "decision": "revise",
        "deficiency_is_genuine": "deficient",
        "target_phase": "replicate",
        "reason": "step 3 emitted no artifact",
        "directive": "run step 3 at full scale and save results.csv",
        "already_tried": "ran at toy scale",
        "confidence": 0.8,
    })
    assert v.decision == DECISION_REVISE
    assert v.target_phase == "replicate"
    assert v.deficiency_is_genuine == GENUINENESS_DEFICIENT
    assert v.directive
    assert v.confidence == 0.8


def test_parse_revise_without_directive_downgrades_to_accept():
    # A revise with no actionable directive must never trigger a blank re-run.
    v = parse_manager_verdict({"decision": "revise", "directive": ""})
    assert v.decision == DECISION_ACCEPT
    assert "downgraded" in v.reason


def test_parse_revise_defaults_target_to_replicate():
    v = parse_manager_verdict({"decision": "revise", "directive": "do X", "target_phase": None})
    assert v.decision == DECISION_REVISE
    assert v.target_phase == "replicate"


def test_parse_unknown_decision_biases_to_accept():
    v = parse_manager_verdict({"decision": "banana"})
    assert v.decision == DECISION_ACCEPT


def test_parse_long_genuineness_phrasings():
    assert parse_manager_verdict(
        {"deficiency_is_genuine": "the work is deficient", "decision": "accept"}
    ).deficiency_is_genuine == GENUINENESS_DEFICIENT
    assert parse_manager_verdict(
        {"deficiency_is_genuine": "irreducible tolerance gap", "decision": "accept"}
    ).deficiency_is_genuine == GENUINENESS_IRREDUCIBLE
    assert parse_manager_verdict(
        {"deficiency_is_genuine": "diligent but result genuinely diverges", "decision": "accept"}
    ).deficiency_is_genuine == GENUINENESS_DIVERGENT


def test_parse_retains_research_requests_phase3():
    # Phase 3: research_requests are retained (dict-shaped only) so the intent
    # allow-list (research.honor_request) can gate them downstream. The parse
    # step no longer strips them — honoring is a single auditable gate.
    v = parse_manager_verdict({
        "decision": "revise", "directive": "do X",
        "research_requests": [
            {"kind": "resource", "need": "dataset"},
            "not-a-dict",  # non-dict entries dropped
        ],
    })
    assert v.research_requests == [{"kind": "resource", "need": "dataset"}]


def test_parse_bad_confidence_defaults_zero():
    v = parse_manager_verdict({"decision": "accept", "confidence": "high"})
    assert v.confidence == 0.0


def test_parse_unknown_target_phase_nulled_on_accept():
    v = parse_manager_verdict({"decision": "accept", "target_phase": "frobnicate"})
    assert v.target_phase is None


# --- no-progress comparison over objective execution facts ------------------
#
# There is no deterministic short-circuit-accept: the manager always judges.
# These tests only cover the mechanical "did the run make headway?" check.


def test_facts_improved_when_failed_steps_drop():
    prev = _negative_facts(failed_steps=2)
    curr = _negative_facts(failed_steps=1)
    assert facts_improved(prev, curr) is True
    # legacy alias points at the same function
    assert signals_improved is facts_improved


def test_facts_not_improved_when_identical():
    prev = _negative_facts(failed_steps=2)
    curr = _negative_facts(failed_steps=2)
    assert facts_improved(prev, curr) is False


def test_facts_improved_when_evidence_appears():
    prev = ExecutionFacts(no_evidence=True)
    curr = _negative_facts(failed_steps=1)
    assert facts_improved(prev, curr) is True


def test_facts_improved_when_missing_steps_drop():
    prev = _negative_facts(missing_steps=[2, 3], failed_steps=1)
    curr = _negative_facts(missing_steps=[3], failed_steps=1)
    assert facts_improved(prev, curr) is True


def test_facts_improved_when_output_gaps_drop():
    prev = ExecutionFacts(steps_without_output_files=[2, 3])
    curr = ExecutionFacts(steps_without_output_files=[3])
    assert facts_improved(prev, curr) is True


def test_facts_improved_none_baseline():
    assert facts_improved(None, _negative_facts()) is True


# --- termination predicate --------------------------------------------------


def test_should_stop_on_accept():
    d = should_stop(
        verdict=ManagerVerdict(decision="accept"),
        iteration=1, max_iters=3,
        prev_signals=None, curr_signals=_clean_signals(), prev_directive=None,
    )
    assert d.stop and d.reason == "accepted"


def test_should_stop_at_cap_without_accept():
    d = should_stop(
        verdict=ManagerVerdict(decision="revise", directive="do X"),
        iteration=3, max_iters=3,
        prev_signals=_negative_signals(), curr_signals=_negative_signals(),
        prev_directive="do Y",
    )
    assert d.stop and d.reason == "cap"


def test_should_continue_when_progress_and_budget():
    prev = _negative_facts(failed_steps=2)
    curr = _negative_facts(failed_steps=1)  # improved
    d = should_stop(
        verdict=ManagerVerdict(decision="revise", directive="new thing"),
        iteration=1, max_iters=3,
        prev_signals=prev, curr_signals=curr, prev_directive="old thing",
    )
    assert not d.stop and d.reason == "continue"


def test_should_stop_no_progress_repeated_directive():
    # No improvement AND the directive repeats -> stuck terminator fires.
    sig = _negative_facts(failed_steps=2)
    d = should_stop(
        verdict=ManagerVerdict(decision="revise", directive="do the SAME thing"),
        iteration=2, max_iters=5,
        prev_signals=sig, curr_signals=sig, prev_directive="do the same THING",
    )
    assert d.stop and d.reason == "no-progress"


def test_no_progress_not_triggered_when_directive_changes():
    sig = _negative_facts(failed_steps=2)
    d = should_stop(
        verdict=ManagerVerdict(decision="revise", directive="a brand new approach"),
        iteration=2, max_iters=5,
        prev_signals=sig, curr_signals=sig, prev_directive="the old approach",
    )
    assert not d.stop


# --- archival ---------------------------------------------------------------


def test_archive_attempt_copies_and_preserves(tmp_path):
    rep = tmp_path / "replication"
    rep.mkdir()
    (rep / "replication_log.json").write_text("{}", encoding="utf-8")
    (rep / "codebase").mkdir()
    (rep / "codebase" / "f.py").write_text("x = 1", encoding="utf-8")

    archive = archive_attempt(rep, 1)
    assert archive is not None
    assert archive.name == "replication.attempt-1"
    # original preserved (copy, not move)
    assert (rep / "replication_log.json").exists()
    # archive holds the snapshot
    assert (archive / "codebase" / "f.py").read_text() == "x = 1"


def test_archive_attempt_none_when_missing(tmp_path):
    assert archive_attempt(tmp_path / "nope", 1) is None


def test_archive_attempt_idempotent_overwrite(tmp_path):
    rep = tmp_path / "replication"
    rep.mkdir()
    (rep / "a.txt").write_text("first", encoding="utf-8")
    archive_attempt(rep, 1)
    (rep / "a.txt").write_text("second", encoding="utf-8")
    archive = archive_attempt(rep, 1)  # same N again
    assert (archive / "a.txt").read_text() == "second"


# --- workflow log -----------------------------------------------------------


def test_workflow_log_append_and_read(tmp_path):
    wf = WorkflowLog(tmp_path / ".veritas")
    wf.append({"iteration": 1, "phase": "replicate", "status": "completed"})
    wf.append({
        "iteration": 1, "phase": "manager_review", "status": "revise",
        "manager_verdict": {"decision": "revise", "directive": "do X", "source": "llm"},
        "directive": "do X",
    })
    recs = wf.records()
    assert len(recs) == 2
    assert recs[0]["phase"] == "replicate"
    assert recs[1]["manager_verdict"]["decision"] == "revise"
    # markdown summary regenerated and mentions the directive
    md = (tmp_path / ".veritas" / "workflow.md").read_text(encoding="utf-8")
    assert "do X" in md and "Iteration 1" in md


def test_workflow_log_skips_malformed_lines(tmp_path):
    wf = WorkflowLog(tmp_path / ".veritas")
    wf.append({"iteration": 1, "phase": "replicate"})
    # corrupt the file with a junk line
    with open(wf.jsonl_path, "a", encoding="utf-8") as f:
        f.write("not json\n")
    recs = wf.records()
    assert len(recs) == 1  # junk line skipped, valid record kept


def test_workflow_handoff_roundtrip(tmp_path):
    wf = WorkflowLog(tmp_path / ".veritas")
    handoff = {"iteration": 3, "where_it_falls_short": "step 3 never produced its CSV",
               "what_to_try_next": "obtain the dataset"}
    wf.write_handoff(handoff)
    got = wf.latest_handoff()
    assert got["where_it_falls_short"] == "step 3 never produced its CSV"
    md = wf.md_path.read_text(encoding="utf-8")
    assert "UNRESOLVED HAND-OFF" in md and "step 3 never produced its CSV" in md


# --- handoff builder --------------------------------------------------------


def test_build_handoff_cap():
    # Objective facts: step 3 failed (nonzero exit) and a planned step is missing.
    sig = ExecutionFacts(missing_step_ids=[4], failed_steps=1, failed_step_ids=[3])
    v = ManagerVerdict(decision="revise", reason="still failing on step 3",
                       directive="get the data", deficiency_is_genuine="deficient")
    h = build_handoff(iteration=3, verdict=v, signals=sig, stop_reason="cap")
    assert h["resolved"] is False
    assert h["stop_reason"] == "cap"
    assert "step 3" in h["where_it_falls_short"]
    # The objective facts are grounded into the hand-off.
    assert "nonzero exit code" in h["where_it_falls_short"]
    assert "[3]" in h["where_it_falls_short"]
    assert h["what_to_try_next"] == "get the data"


def test_build_handoff_no_progress():
    v = ManagerVerdict(decision="revise", reason="r", directive="d")
    h = build_handoff(iteration=2, verdict=v, signals=None, stop_reason="no-progress")
    assert "did not improve" in h["why_rerun_needed"]


# --- guidance bundle --------------------------------------------------------


def test_guidance_from_verdict():
    v = ManagerVerdict(decision="revise", reason="step 3 weak",
                       directive="rerun step 3 at scale", already_tried="toy run")
    g = ManagerGuidance.from_verdict(v, iteration=2)
    assert g.iteration == 2
    assert g.deficiency == "step 3 weak"
    assert g.directive == "rerun step 3 at scale"
    assert g.already_tried == "toy run"
