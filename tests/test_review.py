"""Read-mode (``--depth read``) static-review: models, parsing, config, report."""

import json
import tempfile
from pathlib import Path

import pytest

from veritas.core.config import Config
from veritas.core.models.review import (
    ClaimAssessment,
    ReproducibilityAssessment,
    summarize_support,
)
from veritas.core.models.paper_claims import PaperClaim, PaperClaims, Provenance
from veritas.core.review import parse_review_response
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator


def _paper_and_repo(tmp: Path):
    paper = tmp / "p.pdf"
    paper.write_text("x")
    repo = tmp / "repo"
    repo.mkdir()
    return paper, repo


# -- models round-trip ------------------------------------------------------

def test_claim_assessment_round_trip():
    a = ClaimAssessment(
        claim_id="C1", support_level="partial", reproducibility_risk="high",
        rationale="r", code_location="src/a.py:f", data_available=False,
        issues=["no seed"], evidence_refs=["sec 3"], anchor_quote="q",
    )
    assert ClaimAssessment.from_dict(a.to_dict()) == a


def test_claim_assessment_optional_fields_omitted():
    a = ClaimAssessment(claim_id="C1", support_level="supported")
    d = a.to_dict()
    assert "code_location" not in d and "data_available" not in d and "anchor_quote" not in d


def test_reproducibility_assessment_round_trip():
    agg = ReproducibilityAssessment(
        overall_risk="high", specification="partial", code_coverage="poor",
        data_availability="poor", summary="s", strengths=["a"], weaknesses=["b"],
        recommendation="rec", support_breakdown={"supported": 1}, total_claims=1,
    )
    assert ReproducibilityAssessment.from_dict(agg.to_dict()) == agg


def test_summarize_support_counts_all_levels():
    assessments = [
        ClaimAssessment(claim_id="C1", support_level="supported"),
        ClaimAssessment(claim_id="C2", support_level="partial"),
        ClaimAssessment(claim_id="C3", support_level="partial"),
    ]
    counts = summarize_support(assessments)
    assert counts == {"supported": 1, "partial": 2, "unsupported": 0, "not_assessable": 0}


# -- parsing ----------------------------------------------------------------

def test_parse_review_response_recomputes_breakdown():
    # Agent's own counts are wrong on purpose; parser must recompute from claims.
    payload = json.dumps({
        "overall_risk": "medium",
        "specification": "good",
        "support_breakdown": {"supported": 99},  # bogus — must be overwritten
        "total_claims": 99,                        # bogus — must be overwritten
        "claims": [
            {"claim_id": "C1", "support_level": "supported"},
            {"claim_id": "C2", "support_level": "unsupported"},
        ],
    })
    agg, assessments = parse_review_response(payload)
    assert len(assessments) == 2
    assert agg.total_claims == 2
    assert agg.support_breakdown == {
        "supported": 1, "partial": 0, "unsupported": 1, "not_assessable": 0,
    }


def test_parse_review_response_tolerates_markdown_fence():
    payload = "```json\n" + json.dumps({"overall_risk": "low", "claims": []}) + "\n```"
    agg, assessments = parse_review_response(payload)
    assert assessments == []
    assert agg.overall_risk == "low"


def test_parse_review_response_rejects_non_object():
    with pytest.raises(ValueError):
        parse_review_response("[1, 2, 3]")


# -- config -----------------------------------------------------------------

def test_read_mode_paper_only_infers_paper_only_and_no_repo():
    with tempfile.TemporaryDirectory() as t:
        paper, _ = _paper_and_repo(Path(t))
        cfg = Config(paper_path=paper, output_dir=Path(t) / "out", depth="read")
        assert cfg.mode == "paper-only" and cfg.depth == "read"
        assert cfg.effective_repo_path is None


def test_read_mode_full_uses_user_repo_not_codegen():
    with tempfile.TemporaryDirectory() as t:
        paper, repo = _paper_and_repo(Path(t))
        cfg = Config(paper_path=paper, repo_path=repo, output_dir=Path(t) / "out", depth="read")
        assert cfg.mode == "full"
        assert cfg.effective_repo_path == repo  # never the codegen codebase


def test_read_mode_requires_paper():
    with tempfile.TemporaryDirectory() as t:
        _, repo = _paper_and_repo(Path(t))
        with pytest.raises(ValueError):
            Config(repo_path=repo, output_dir=Path(t) / "out", depth="read")


def test_invalid_depth_rejected():
    with tempfile.TemporaryDirectory() as t:
        paper, _ = _paper_and_repo(Path(t))
        with pytest.raises(ValueError):
            Config(paper_path=paper, output_dir=Path(t) / "out", depth="deep")


def test_inline_without_paper_is_disabled():
    with tempfile.TemporaryDirectory() as t:
        _, repo = _paper_and_repo(Path(t))
        cfg = Config(repo_path=repo, output_dir=Path(t) / "out", emit_inline=True)
        assert cfg.emit_inline is False


def test_depth_in_config_fingerprint():
    from veritas.core.runner import ReplicationRunner
    with tempfile.TemporaryDirectory() as t:
        paper, _ = _paper_and_repo(Path(t))
        cfg = Config(paper_path=paper, output_dir=Path(t) / "out", depth="read")
        fp = ReplicationRunner(cfg)._config_fingerprint()
        assert fp["depth"] == "read"


# -- prompt + report --------------------------------------------------------

def test_static_review_prompt_forbids_execution_and_includes_paths():
    with tempfile.TemporaryDirectory() as t:
        paper, repo = _paper_and_repo(Path(t))
        prompt = PromptGenerator().generate_static_review_prompt(
            paper_path=paper, output_dir=Path(t) / "out", repo_path=repo,
        )
        assert "do NOT run code" in prompt or "not run" in prompt.lower()
        assert str(paper.absolute()) in prompt
        assert str(repo.absolute()) in prompt


def test_review_report_renders_md_html_pdf():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        paper, _ = _paper_and_repo(tmp)
        cfg = Config(paper_path=paper, output_dir=tmp / "out", depth="read")
        claims = PaperClaims(paper={"title": "T"}, claims=[
            PaperClaim(id="C1", description="d1", type="scalar", tier="headline"),
            PaperClaim(id="C2", description="d2", type="qualitative", tier="supporting"),
        ])
        assessments = [
            ClaimAssessment(claim_id="C1", support_level="partial", reproducibility_risk="high", rationale="r1"),
            ClaimAssessment(claim_id="C2", support_level="supported", reproducibility_risk="low", rationale="r2"),
        ]
        agg = ReproducibilityAssessment(
            overall_risk="high", specification="partial", code_coverage="poor",
            data_availability="poor", summary="sum", support_breakdown=summarize_support(assessments),
            total_claims=2,
        )
        md_path, pdf_path = ReportGenerator().generate_review_report(
            claims=claims, aggregate=agg, assessments=assessments,
            config=cfg, output_dir=cfg.output_dir, generate_pdf=False,
        )
        assert md_path.exists()
        html = (cfg.report_dir / "replication_report.html").read_text()
        assert "Reproducibility Assessment" in html
        assert "no code executed" in html.lower() or "no code was executed" in html.lower()
        md = md_path.read_text()
        assert "Overall reproducibility risk: HIGH" in md
        assert "C1" in md and "C2" in md
