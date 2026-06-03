"""Unit + structure tests for the manager research sub-agents (Phase 3).

Deterministic pieces (intent gate, exact known-value scrub, provenance
formatting, request parsing, bounds) are pure-function unit-tested here. The
agent/LLM pieces (finder dispatch, LLM redaction wiring) are covered as structure
tests that stub ``_invoke_provider`` so no LLM or Docker is touched — they assert
request dispatch, the redaction wiring (LLM + deterministic scrub), provenance
tagging, the per-iteration cap, and the workflow-log records.
"""

from __future__ import annotations

import json

from veritas.core.config import Config
from veritas.core.manager import ManagerVerdict, WorkflowLog
from veritas.core.models.paper_claims import PaperClaim, PaperClaims
from veritas.core.pipeline_state import PipelineState
from veritas.core.research import (
    KIND_LITERATURE,
    KIND_RESOURCE,
    REDACTION_MARKER,
    ResearchConfig,
    ResearchFinding,
    ResearchRequest,
    format_findings_for_guidance,
    honor_request,
    known_value_strings,
    parse_research_requests,
    redact_known_values,
    split_requests,
)
from veritas.core.runner import ReplicationRunner

# ----------------------------------------------------------------------------
# Barrier (a): intent allow-list (deterministic, structured-field check)
# ----------------------------------------------------------------------------


def test_intent_gate_honors_resource_and_literature():
    assert honor_request(ResearchRequest(kind="resource", need="dataset X"))
    assert honor_request(ResearchRequest(kind="literature", need="standard LR"))


def test_intent_gate_rejects_answer_seeking_and_unknown_kinds():
    # An answer-seeking request cannot carry a valid resource/literature kind.
    assert not honor_request(
        ResearchRequest(kind="answer", need="find the reported accuracy of model X")
    )
    assert not honor_request(ResearchRequest(kind="", need="something"))
    assert not honor_request(ResearchRequest(kind="result", need="the F1 they report"))


def test_split_requests_partitions_honored_vs_rejected():
    reqs = [
        ResearchRequest(kind="resource", need="get dataset"),
        ResearchRequest(kind="answer", need="reported BLEU"),
        ResearchRequest(kind="literature", need="preprocessing"),
    ]
    honored, rejected = split_requests(reqs)
    assert [r.need for r in honored] == ["get dataset", "preprocessing"]
    assert [r.need for r in rejected] == ["reported BLEU"]


def test_parse_research_requests_tolerant():
    raw = [
        {"kind": "resource", "need": "dataset", "rationale": "blocked"},
        {"kind": "literature", "need": ""},  # empty need dropped
        "garbage",  # non-dict dropped
        {"need": "no kind ok at parse"},  # kept; gate decides later
    ]
    parsed = parse_research_requests(raw)
    assert [r.need for r in parsed] == ["dataset", "no kind ok at parse"]
    assert parsed[0].kind == "resource"
    assert parse_research_requests("not a list") == []


# ----------------------------------------------------------------------------
# Barrier (b), deterministic layer: exact known-value scrub
# ----------------------------------------------------------------------------


def test_known_value_strings_flattens_shapes():
    vals = known_value_strings([
        "0.873",            # scalar
        [0.1, 0.2],          # range
        {"row1": "12.5", "row2": "13.5"},  # table
        None,                # ignored
        "x",                 # too short, dropped
    ])
    assert "0.873" in vals
    assert "0.1" in vals and "0.2" in vals
    assert "12.5" in vals and "13.5" in vals
    assert "x" not in vals
    # deduplicated
    assert len(vals) == len(set(vals))


def test_redact_known_values_scrubs_exact_only():
    text = "Use the SST-2 dataset from https://example.com. Authors report 91.4 accuracy."
    res = redact_known_values(text, ["91.4"])
    assert "91.4" not in res.redacted_text
    assert REDACTION_MARKER in res.redacted_text
    assert res.exact_hits == ["91.4"]
    # methodology / URL preserved
    assert "SST-2" in res.redacted_text
    assert "https://example.com" in res.redacted_text


def test_redact_known_values_no_keyword_guessing():
    # A number that is NOT a known value is left alone — no keyword/regex bank
    # guessing at what "looks like" an answer.
    text = "Set learning rate to 0.0003 for 50 epochs."
    res = redact_known_values(text, ["91.4"])
    assert res.redacted_text == text
    assert res.exact_hits == []


# ----------------------------------------------------------------------------
# Barrier (c): provenance-tagged injection
# ----------------------------------------------------------------------------


def test_format_findings_includes_provenance_and_skips_empty():
    findings = [
        ResearchFinding(kind=KIND_RESOURCE, need="dataset",
                        finding="Download via get_data.sh", sources=["https://repo/x"]),
        ResearchFinding(kind=KIND_LITERATURE, need="lr", finding="", error="not found"),
    ]
    text = format_findings_for_guidance(findings)
    assert "get_data.sh" in text
    assert "https://repo/x" in text
    assert "[resource]" in text
    # empty/errored finding is skipped
    assert text.count("need:") == 1


def test_format_findings_empty_when_nothing_usable():
    assert format_findings_for_guidance([]) == ""
    assert format_findings_for_guidance(
        [ResearchFinding(kind=KIND_RESOURCE, need="x", finding="", error="e")]
    ) == ""


def test_format_findings_discards_unsourced():
    # Provenance is mandatory (barrier c): a finding with no source is dropped.
    assert format_findings_for_guidance(
        [ResearchFinding(kind=KIND_RESOURCE, need="x", finding="some method", sources=[])]
    ) == ""
    assert format_findings_for_guidance(
        [ResearchFinding(kind=KIND_RESOURCE, need="x", finding="m", sources=["  "])]
    ) == ""


# ----------------------------------------------------------------------------
# Bounds / config
# ----------------------------------------------------------------------------


def test_research_config_default_and_env(monkeypatch):
    monkeypatch.delenv("VERITAS_RESEARCH_MAX_CALLS", raising=False)
    assert ResearchConfig.from_env().max_calls_per_iteration == 2
    monkeypatch.setenv("VERITAS_RESEARCH_MAX_CALLS", "1")
    assert ResearchConfig.from_env().max_calls_per_iteration == 1
    monkeypatch.setenv("VERITAS_RESEARCH_MAX_CALLS", "0")
    assert ResearchConfig.from_env().max_calls_per_iteration == 0


# ----------------------------------------------------------------------------
# Structure tests: runner wiring (dispatch + redaction + provenance + bounds)
# ----------------------------------------------------------------------------


def _make_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    out = tmp_path / "out"
    cfg = Config(repo_path=repo, output_dir=out, mode="repo-only", max_iters=3)
    (out / "replication").mkdir(parents=True, exist_ok=True)
    (out / ".veritas").mkdir(parents=True, exist_ok=True)
    (out / "prompts").mkdir(parents=True, exist_ok=True)
    return cfg


def _claims_with_value(value="91.4"):
    return PaperClaims(claims=[
        PaperClaim(id="C1", description="headline", type="scalar", tier="headline",
                   paper_value=value),
    ])


def test_run_research_rejects_answer_seeking_request(tmp_path, monkeypatch):
    """Intent gate: an answer-seeking request is rejected, no sub-agent dispatched."""
    cfg = _make_config(tmp_path)
    runner = ReplicationRunner(cfg)
    workflow = WorkflowLog(cfg.veritas_state_dir)

    dispatched = []
    monkeypatch.setattr(
        runner, "_dispatch_research_agent",
        lambda req, index: dispatched.append(req) or ResearchFinding(
            kind=req.kind, need=req.need, finding="x", sources=["u"]),
    )

    verdict = ManagerVerdict(
        decision="revise", directive="d",
        research_requests=[{"kind": "answer", "need": "find the reported accuracy"}],
    )
    text = runner._run_research(verdict, _claims_with_value(), iteration=1, workflow=workflow)

    assert text == ""
    assert dispatched == []  # nothing honored -> nothing dispatched
    recs = workflow.records()
    research = [r for r in recs if r["phase"] == "research"][0]["research"]
    assert len(research["rejected"]) == 1
    assert research["honored"] == []


def test_run_research_dispatches_redacts_and_injects(tmp_path, monkeypatch):
    """End-to-end wiring: honored request -> finder returns finding with a fake
    reported value embedded -> redactor (LLM + deterministic scrub) removes it ->
    only methodology + provenance reach the injected guidance + workflow log."""
    cfg = _make_config(tmp_path)
    runner = ReplicationRunner(cfg)
    workflow = WorkflowLog(cfg.veritas_state_dir)

    # Stub the finder: returns a finding that embeds the known paper value 91.4.
    def fake_dispatch(req, index):
        return ResearchFinding(
            kind=req.kind, need=req.need,
            finding="Get SST-2 from https://repo/x. (Note: paper reports 91.4 accuracy.)",
            sources=["https://repo/x"],
        )

    monkeypatch.setattr(runner, "_dispatch_research_agent", fake_dispatch)

    # Stub the LLM redactor: simulate the provider writing a redaction JSON that
    # the LLM judged but (deliberately) left the value in, so we can assert the
    # deterministic belt-and-suspenders scrub also fires.
    def fake_invoke(prompt, working_dir, log_path, timeout, **kw):
        # The redactor is the only _invoke_provider call here (dispatch is stubbed).
        # Write a redaction result that keeps the value, so the exact scrub catches it.
        out = cfg.research_redaction_path("resource", 0)
        out.write_text(json.dumps({
            "redacted_finding": "Get SST-2 from https://repo/x. (Note: paper reports 91.4 accuracy.)",
            "removed_anything": False,
            "removed_summary": "nothing",
        }), encoding="utf-8")
        return True

    monkeypatch.setattr(runner, "_invoke_provider", fake_invoke)

    verdict = ManagerVerdict(
        decision="revise", directive="d",
        research_requests=[{"kind": "resource", "need": "SST-2 dataset", "rationale": "missing data"}],
    )
    text = runner._run_research(verdict, _claims_with_value("91.4"), iteration=1, workflow=workflow)

    # The known reported value is scrubbed out of the injected guidance...
    assert "91.4" not in text
    assert REDACTION_MARKER in text
    # ...but the methodology + provenance survive.
    assert "SST-2" in text
    assert "https://repo/x" in text

    # Workflow log records the finding, the redaction (exact hit), and the injection.
    research = [r for r in workflow.records() if r["phase"] == "research"][0]["research"]
    f0 = research["findings"][0]
    assert "91.4" not in f0["finding"]
    assert f0["redaction"]["exact_hits"] == ["91.4"]
    assert "91.4" not in research["injected_guidance"]
    assert research["honored"][0]["need"] == "SST-2 dataset"


def test_run_research_respects_per_iteration_cap(tmp_path, monkeypatch):
    """Bounds: with cap=1, only one of two honored requests is dispatched."""
    cfg = _make_config(tmp_path)
    monkeypatch.setenv("VERITAS_RESEARCH_MAX_CALLS", "1")
    runner = ReplicationRunner(cfg)
    workflow = WorkflowLog(cfg.veritas_state_dir)

    calls = []

    def fake_dispatch(req, index):
        calls.append(req.kind)
        return ResearchFinding(kind=req.kind, need=req.need, finding="m", sources=["u"])

    monkeypatch.setattr(runner, "_dispatch_research_agent", fake_dispatch)
    # No redactor LLM call needed beyond the finding; stub it to a no-op success
    # that writes an identity redaction so the wiring completes.
    def fake_invoke(prompt, working_dir, log_path, timeout, **kw):
        # write identity redaction for whichever kind/index is being redacted
        return False  # force fall-closed to deterministic scrub of original
    monkeypatch.setattr(runner, "_invoke_provider", fake_invoke)

    verdict = ManagerVerdict(
        decision="revise", directive="d",
        research_requests=[
            {"kind": "resource", "need": "dataset A"},
            {"kind": "literature", "need": "method B"},
        ],
    )
    runner._run_research(verdict, _claims_with_value(), iteration=1, workflow=workflow)

    assert len(calls) == 1  # capped to 1 dispatch
    research = [r for r in workflow.records() if r["phase"] == "research"][0]["research"]
    # "honored" in the record is the dispatched subset (post-cap); the rest land
    # in "dropped_for_cap". Both passed the intent gate; the cap bounded fan-out.
    assert len(research["honored"]) == 1
    assert len(research["dropped_for_cap"]) == 1


def test_run_research_disabled_when_cap_zero(tmp_path, monkeypatch):
    """Bounds: cap=0 disables research even when the manager requests it."""
    cfg = _make_config(tmp_path)
    monkeypatch.setenv("VERITAS_RESEARCH_MAX_CALLS", "0")
    runner = ReplicationRunner(cfg)
    workflow = WorkflowLog(cfg.veritas_state_dir)

    dispatched = []
    monkeypatch.setattr(
        runner, "_dispatch_research_agent",
        lambda req, index: dispatched.append(req),
    )
    verdict = ManagerVerdict(
        decision="revise", directive="d",
        research_requests=[{"kind": "resource", "need": "dataset"}],
    )
    text = runner._run_research(verdict, _claims_with_value(), iteration=1, workflow=workflow)
    assert text == ""
    assert dispatched == []


def test_run_research_no_requests_returns_empty(tmp_path):
    cfg = _make_config(tmp_path)
    runner = ReplicationRunner(cfg)
    workflow = WorkflowLog(cfg.veritas_state_dir)
    verdict = ManagerVerdict(decision="revise", directive="d", research_requests=[])
    assert runner._run_research(verdict, _claims_with_value(), iteration=1, workflow=workflow) == ""
    # no research record written when there were no requests at all
    assert [r for r in workflow.records() if r["phase"] == "research"] == []


def test_loop_research_findings_reach_rerun_guidance(tmp_path, monkeypatch):
    """End-to-end through the real loop: manager revise + resource request ->
    resource-finder returns a finding with an embedded fake reported value ->
    redactor removes it -> only methodology (with provenance) reaches the re-run
    replicate agent's manager_guidance.research_findings."""
    from veritas.core.diligence import ExecutionFacts
    from veritas.core.models.replication import (
        ExecutionEvidence,
        ReplicationPlan,
        ReplicationStep,
        StepOutcome,
    )

    cfg = _make_config(tmp_path)
    (cfg.replication_dir / "replication_log.json").write_text("{}", encoding="utf-8")
    runner = ReplicationRunner(cfg)
    state = PipelineState(cfg.output_dir)

    def neg_facts():
        f = ExecutionFacts()
        f.failed_steps = 1
        f.failed_step_ids = [1]
        return f

    guidance_seen = []

    def fake_replicate(plan, manager_guidance=None):
        guidance_seen.append(manager_guidance)
        (cfg.replication_dir / "replication_log.json").write_text(
            json.dumps({"a": len(guidance_seen)}), encoding="utf-8")
        runner._last_facts = neg_facts()
        return ExecutionEvidence(environment={}, step_outcomes=[
            StepOutcome(step_id=1, description="run", command_executed="x", exit_code=0)])

    # Manager: revise with a resource request on iter 1, then accept.
    reviews = iter([
        ManagerVerdict(decision="revise", target_phase="replicate",
                       reason="missing dataset", directive="fetch the dataset and run",
                       deficiency_is_genuine="deficient",
                       research_requests=[{"kind": "resource", "need": "SST-2 dataset",
                                           "rationale": "data missing"}]),
        ManagerVerdict(decision="accept", reason="now diligent"),
    ])
    monkeypatch.setattr(runner, "_replicate", fake_replicate)
    monkeypatch.setattr(runner, "_manager_review", lambda *a, **k: next(reviews))

    # resource-finder returns a finding embedding the known paper value 91.4.
    monkeypatch.setattr(
        runner, "_dispatch_research_agent",
        lambda req, index: ResearchFinding(
            kind=req.kind, need=req.need,
            finding="Download SST-2 from https://repo/sst2 (paper reports 91.4 acc).",
            sources=["https://repo/sst2"]),
    )
    # LLM redactor fails (fall-closed) so the deterministic exact scrub of the
    # known paper value (91.4) is what removes it — proving the belt-and-suspenders.
    monkeypatch.setattr(runner, "_invoke_provider",
                        lambda *a, **k: False)

    claims = _claims_with_value("91.4")
    plan = ReplicationPlan(environment={}, steps=[
        ReplicationStep(id=1, description="run", command_hint="x", expected_outcome="r.csv")])

    runner._replicate_with_manager_loop(state, claims, plan)

    # Two replicate runs; the re-run carried research findings in its guidance.
    assert len(guidance_seen) == 2
    assert guidance_seen[0] is None
    rerun_findings = guidance_seen[1].research_findings
    assert "91.4" not in rerun_findings            # reported value scrubbed
    assert REDACTION_MARKER in rerun_findings
    assert "https://repo/sst2" in rerun_findings   # provenance survives
    assert "SST-2" in rerun_findings               # methodology survives

    # Workflow log has a research record between the reviews.
    phases = [r["phase"] for r in workflow_phases(cfg)]
    assert "research" in phases


def workflow_phases(cfg):
    return [json.loads(ln) for ln in cfg.workflow_log_path.read_text().splitlines() if ln.strip()]
