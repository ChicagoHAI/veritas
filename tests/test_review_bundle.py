"""ReviewBundle + exporters: field mappings to OpenAIReview-viz and sai-web."""

import json

from veritas.core.inline import Comment
from veritas.core.review_bundle import (
    ReviewBundle,
    to_oar_viz,
    to_saiweb_demo,
    to_saiweb_review,
    write_bundle,
)


def _bundle():
    return ReviewBundle(
        slug="demo", title="A Paper", mode="paper-only", depth="read",
        paragraphs=["Intro paragraph.", "We report a 12% effect.", "Methods here."],
        comments=[
            Comment(id="c0", title="Overclaim", quote="12% effect", explanation="weak",
                    category="claim-support", severity="major", paragraph_index=1),
            Comment(id="c1", title="No seed", quote="Methods here", explanation="seed?",
                    category="technical", severity="moderate", paragraph_index=2),
            Comment(id="c2", title="Stat", quote="", explanation="p-hack",
                    category="statistical", severity="minor", paragraph_index=None),
        ],
        overall_feedback="Overall ok.",
        verdict_sections=[{"heading": "Decision", "body": "Major revision."}],
        reproducibility={"recommendation": "Release the data."},
        engine_meta={"model": "openai/gpt-4o", "prompt_tokens": 100, "completion_tokens": 20},
    )


def test_bundle_round_trip():
    b = _bundle()
    b2 = ReviewBundle.from_dict(b.to_dict())
    assert b2.slug == b.slug and len(b2.comments) == 3 and b2.paragraphs == b.paragraphs


def test_oar_viz_schema_and_mappings():
    viz = to_oar_viz(_bundle())
    assert set(viz) == {"slug", "title", "paragraphs", "methods"}
    assert viz["paragraphs"][1] == {"index": 1, "text": "We report a 12% effect."}
    m = viz["methods"]["veritas"]
    assert m["model"] == "openai/gpt-4o" and m["prompt_tokens"] == 100
    c0, c1, c2 = m["comments"]
    # category -> comment_type
    assert c0["comment_type"] == "logical"      # claim-support
    assert c1["comment_type"] == "technical"    # technical
    assert c2["comment_type"] == "technical"    # statistical
    # severity -> OAR severity (info would map to minor; here major/moderate/minor)
    assert c0["severity"] == "major" and c1["severity"] == "moderate" and c2["severity"] == "minor"
    assert c0["paragraph_index"] == 1


def test_saiweb_demo_schema_and_hascomment():
    demo = to_saiweb_demo(_bundle())
    assert set(demo) == {"slug", "title", "overallFeedback", "paragraphs", "comments"}
    # hasComment is true only for paragraphs that have an anchored comment (1 and 2)
    assert demo["paragraphs"][0]["hasComment"] is False
    assert demo["paragraphs"][1]["hasComment"] is True
    assert demo["paragraphs"][2]["hasComment"] is True
    c = demo["comments"][0]
    assert set(c) == {"id", "title", "quote", "explanation", "commentType", "paragraphIndex"}
    assert c["commentType"] == "logical" and c["paragraphIndex"] == 1


def test_saiweb_review_severity_and_verdict():
    rev = to_saiweb_review(_bundle(), project_id="p1")
    assert rev["id"] == "review_p1" and rev["projectId"] == "p1"
    assert rev["verdict"] == [{"heading": "Decision", "body": "Major revision."}]
    assert rev["recommendation"] == "Release the data."
    sev = [c["severity"] for c in rev["comments"]]
    assert sev == ["Major", "Minor", "Minor"]  # major/moderate/minor -> Major/Minor/Minor
    assert rev["methods"][0]["commentCount"] == 3
    assert rev["inlineSummary"] == "Overall ok."


def test_assemble_bundle_from_output_run_mode(tmp_path):
    from veritas.core.review_bundle import assemble_bundle_from_output
    for d in ("analyze", "inline", "verify", "evaluation"):
        (tmp_path / d).mkdir()
    (tmp_path / "analyze" / "paper_claims.json").write_text(
        json.dumps({"paper": {"title": "My Paper"}, "claims": []}))
    (tmp_path / "inline" / "paper_text.json").write_text(
        json.dumps({"paragraphs": ["Intro.", "Result 12%."]}))
    (tmp_path / "inline" / "inline_comments.json").write_text(json.dumps([
        {"id": "oar_0", "title": "t", "quote": "q", "explanation": "e",
         "category": "technical", "severity": "moderate", "paragraph_index": 1},
        {"id": "claim_C1", "title": "replication: did not reproduce", "quote": "Result 12%",
         "explanation": "off", "category": "replication", "severity": "major", "paragraph_index": 1},
    ]))
    (tmp_path / "inline" / "overall_feedback.md").write_text("Overall.")
    (tmp_path / "verify" / "replication_score.json").write_text(
        json.dumps({"score": 0.6, "counted_claims": 5}))
    (tmp_path / "evaluation" / "contextual_evaluation.json").write_text(
        json.dumps({"report": {"replication_summary": "Mostly held."}}))

    b = assemble_bundle_from_output(tmp_path, slug="mypaper")
    assert b.depth == "run" and b.mode == "full"
    assert b.title == "My Paper" and len(b.comments) == 2 and len(b.paragraphs) == 2
    assert b.score["score"] == 0.6
    headings = [s["heading"] for s in b.verdict_sections]
    assert "Replication verdict" in headings and "Summary" in headings
    # Both lenses present in the merged inline comments.
    cats = {c.category for c in b.comments}
    assert "technical" in cats and "replication" in cats
    # Exports work off the assembled bundle.
    demo = to_saiweb_demo(b)
    assert demo["paragraphs"][1]["hasComment"] is True


def test_assemble_bundle_from_output_read_mode(tmp_path):
    from veritas.core.review_bundle import assemble_bundle_from_output
    (tmp_path / "inline").mkdir()
    (tmp_path / "review").mkdir()
    (tmp_path / "inline" / "paper_text.json").write_text(json.dumps({"paragraphs": ["A para here."]}))
    (tmp_path / "inline" / "inline_comments.json").write_text(json.dumps([]))
    (tmp_path / "review" / "reproducibility_assessment.json").write_text(json.dumps({
        "overall_risk": "high", "specification": "good", "code_coverage": "poor",
        "data_availability": "poor", "summary": "Risky.", "weaknesses": ["no seed"],
        "recommendation": "Release data.",
    }))
    b = assemble_bundle_from_output(tmp_path, slug="p")
    assert b.depth == "read" and b.mode == "paper-only"
    headings = [s["heading"] for s in b.verdict_sections]
    assert "Reproducibility verdict" in headings and "Recommendation" in headings


def test_write_bundle(tmp_path):
    paths = write_bundle(_bundle(), tmp_path)
    for key in ("bundle", "oar_viz", "saiweb_demo"):
        assert paths[key].exists()
    viz = json.loads(paths["oar_viz"].read_text())
    assert "methods" in viz
    demo = json.loads(paths["saiweb_demo"].read_text())
    assert len(demo["comments"]) == 3
