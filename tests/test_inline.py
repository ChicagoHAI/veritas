"""In-line comment subsystem: paragraph split, fuzzy anchoring, comment build, viewer."""

import json

from veritas.core.inline import (
    Comment,
    anchor_comments,
    build_claim_comments,
    locate_comment_in_document,
    parse_reviewer_comments,
    render_viewer,
    split_into_paragraphs,
)
from veritas.core.models.paper_claims import (
    ClaimVerdict,
    PaperClaim,
    PaperClaims,
    Provenance,
)
from veritas.core.models.review import ClaimAssessment


# Realistic-length paragraphs (>100 chars so they don't merge).
P1 = "Introduction. " * 12
P2 = (
    "We observe a 12 percent reduction in equality when the allocation maximizes "
    "cooperation, a pattern that holds across all five experimental conditions. "
) * 2
P3 = (
    "Methods. We fit the estimator by ordinary least squares and quantify uncertainty "
    "with a bootstrap of B equals 2000 resamples from the trial level data. "
) * 2
TEXT = P1 + "\n\n" + P2 + "\n\n" + P3


def _claims():
    return PaperClaims(paper={"title": "T"}, claims=[
        PaperClaim(id="C1", description="12% reduction", type="scalar", tier="headline",
                   provenance=Provenance(section="R", page=3,
                                         quote="we observe a 12 percent reduction in equality")),
        PaperClaim(id="C2", description="bootstrap B=2000", type="qualitative", tier="supporting"),
    ])


# -- segmentation + anchoring ----------------------------------------------

def test_split_keeps_long_paragraphs_separate():
    paras = split_into_paragraphs(TEXT)
    assert len(paras) == 3


def test_split_merges_short_paragraphs_forward():
    # Each line is < 100 chars, so all merge into one block (OpenAIReview rule).
    paras = split_into_paragraphs("a\n\nb\n\nc")
    assert len(paras) == 1


def test_locate_exact_and_fuzzy():
    paras = split_into_paragraphs(TEXT)
    assert locate_comment_in_document("we observe a 12 percent reduction in equality", paras) == 1
    assert locate_comment_in_document("bootstrap of B equals 2000 resamples", paras) == 2


def test_locate_empty_quote_returns_none():
    assert locate_comment_in_document("", split_into_paragraphs(TEXT)) is None
    assert locate_comment_in_document("anything", []) is None


def test_anchor_comments_sets_index():
    paras = split_into_paragraphs(TEXT)
    comments = [Comment(id="x", title="t", quote="bootstrap of B equals 2000", explanation="e")]
    anchor_comments(comments, paras)
    assert comments[0].paragraph_index == 2


# -- claim-anchored comments ------------------------------------------------

def test_build_claim_comments_from_assessments_severity():
    assessments = [
        ClaimAssessment(claim_id="C1", support_level="unsupported", reproducibility_risk="high",
                        rationale="no code", issues=["no seed"],
                        anchor_quote="we observe a 12 percent reduction in equality"),
        ClaimAssessment(claim_id="C2", support_level="supported", reproducibility_risk="low",
                        rationale="ok", code_location="boot.R"),
    ]
    cc = build_claim_comments(_claims(), assessments=assessments)
    assert len(cc) == 2
    assert cc[0].severity == "major" and cc[0].category == "claim-support"
    assert cc[1].severity == "info"
    assert cc[0].claim_id == "C1"
    assert "boot.R" in cc[1].explanation  # code_location surfaced


def test_build_claim_comments_from_verdicts_severity():
    verdicts = [
        ClaimVerdict(claim_id="C1", status="no_match", rationale="off by 40%"),
        ClaimVerdict(claim_id="C2", status="match", rationale="ok"),
    ]
    cc = build_claim_comments(_claims(), verdicts=verdicts)
    assert cc[0].severity == "major"
    assert cc[1].severity == "info"


def test_build_claim_comments_uses_provenance_quote_fallback():
    cc = build_claim_comments(_claims())  # no assessments/verdicts
    # C1 has a provenance quote; C2 falls back to its description.
    assert cc[0].quote == "we observe a 12 percent reduction in equality"
    assert cc[1].quote == "bootstrap B=2000"


# -- reviewer parsing -------------------------------------------------------

def test_parse_reviewer_comments_array():
    payload = json.dumps([
        {"title": "No seed", "quote": "bootstrap of B equals 2000", "explanation": "x",
         "category": "reproducibility", "severity": "major"},
    ])
    rev = parse_reviewer_comments(payload)
    assert len(rev) == 1 and rev[0].category == "reproducibility"
    assert rev[0].id.startswith("rev_")


def test_parse_reviewer_comments_accepts_wrapped_object():
    payload = json.dumps({"comments": [{"title": "t", "quote": "q", "explanation": "e"}]})
    assert len(parse_reviewer_comments(payload)) == 1


def test_parse_reviewer_comments_tolerates_fence():
    payload = "```json\n[]\n```"
    assert parse_reviewer_comments(payload) == []


# -- viewer -----------------------------------------------------------------

def test_render_viewer_embeds_data_and_is_self_contained():
    paras = split_into_paragraphs(TEXT)
    comments = build_claim_comments(_claims())
    anchor_comments(comments, paras)
    html = render_viewer("My Paper", "sub", paras, comments)
    assert "<!DOCTYPE html>" in html
    assert 'id="data"' in html  # embedded JSON, no server needed
    assert "My Paper" in html
    # The embedded payload round-trips.
    start = html.index('type="application/json">') + len('type="application/json">')
    end = html.index("</script>", start)
    payload = json.loads(html[start:end])
    assert len(payload["paragraphs"]) == 3
    assert len(payload["comments"]) == 2
