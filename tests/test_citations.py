"""Unit tests for the deterministic citation resolver (core/citations.py).

The HTTP layer is injected (a fake ``fetch_json``) so no network is touched.
Pure-function pieces (parsing, normalization, matching, classification) are
unit-tested directly, mirroring the pure-function test style of test_research.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from veritas.core.config import Config

from veritas.core.citations import (
    Reference,
    SourceRecord,
    CitationVerdict,
    parse_references,
    normalize_title,
    title_similarity,
    author_overlap,
    normalize_arxiv_id,
    best_match,
    classify,
    STATUS_VERIFIED,
    STATUS_METADATA_MISMATCH,
    STATUS_UNRESOLVED,
    TITLE_MATCH_THRESHOLD,
    AUTHOR_OVERLAP_THRESHOLD,
    parse_crossref,
    parse_openalex,
    parse_semantic_scholar,
    parse_dblp,
    parse_arxiv_atom,
    resolve_references,
    build_summary,
)


def test_reference_roundtrips_through_dict():
    ref = Reference(
        key="smith2024", title="Attention Is All You Need",
        authors=["A. Vaswani", "N. Shazeer"], year=2017, venue="NeurIPS",
        doi="", arxiv_id="1706.03762", raw="Vaswani et al. NeurIPS 2017.",
    )
    d = ref.to_dict()
    assert Reference.from_dict(d) == ref


def test_parse_references_tolerates_missing_fields_and_skips_empty():
    raw = json.dumps([
        {"raw": "Some ref", "title": "A Title"},
        {"title": ""},                      # no raw, no usable content -> skipped
        "not a dict",                        # skipped
        {"raw": "Only raw"},
    ])
    refs = parse_references(raw)
    assert [r.title for r in refs] == ["A Title", ""]
    assert [r.raw for r in refs] == ["Some ref", "Only raw"]


def test_verdict_to_dict_shape():
    v = CitationVerdict(
        key="x", title="T", status="metadata_mismatch",
        matched_record=SourceRecord(source="dblp", title="T", authors=["A"], year=2024, venue="ICLR", doi="", url="u"),
        mismatches=["venue: cited 'arXiv' but record 'ICLR 2024'"],
        sources_queried=["crossref", "dblp"],
    )
    d = v.to_dict()
    assert d["status"] == "metadata_mismatch"
    assert d["matched_record"]["source"] == "dblp"
    assert d["mismatches"] == ["venue: cited 'arXiv' but record 'ICLR 2024'"]


def test_normalize_title_strips_punct_case_and_space():
    assert normalize_title("Attention Is All You Need!") == "attention is all you need"
    assert normalize_title("  Deep   Learning  ") == "deep learning"


def test_title_similarity_high_for_near_identical_low_for_different():
    assert title_similarity("Attention is all you need", "Attention Is All You Need!") >= 0.95
    assert title_similarity("Attention is all you need", "A survey of graph networks") < 0.5


def test_author_overlap_by_last_name():
    assert author_overlap(["A. Vaswani", "N. Shazeer"], ["Ashish Vaswani", "Noam Shazeer"]) == 1.0
    assert author_overlap(["A. Vaswani"], ["J. Doe", "Q. Public"]) == 0.0
    # Half the cited authors are present in the record.
    assert abs(author_overlap(["Vaswani", "Smith"], ["Vaswani", "Doe"]) - 0.5) < 1e-9


def test_author_overlap_empty_is_zero():
    assert author_overlap([], ["A. Smith"]) == 0.0
    assert author_overlap(["A. Smith"], []) == 0.0


def test_normalize_arxiv_id_strips_prefix_and_version():
    assert normalize_arxiv_id("arXiv:1706.03762v5") == "1706.03762"
    assert normalize_arxiv_id("1706.03762") == "1706.03762"
    assert normalize_arxiv_id("https://arxiv.org/abs/2401.01234") == "2401.01234"
    assert normalize_arxiv_id("10.1145/3292500") == ""  # a DOI, not an arXiv id


# ---------------------------------------------------------------------------
# --- record matching + verdict classification ---
# ---------------------------------------------------------------------------

def _rec(**kw):
    return SourceRecord(**{"source": "dblp", **kw})


def test_best_match_picks_highest_title_similarity():
    ref = Reference(title="Attention Is All You Need", authors=["Vaswani"])
    recs = [
        _rec(source="crossref", title="A survey of attention", authors=["X"]),
        _rec(source="dblp", title="Attention is all you need", authors=["Vaswani"], venue="NeurIPS", year=2017),
    ]
    rec, sim = best_match(ref, recs)
    assert rec.source == "dblp" and sim >= TITLE_MATCH_THRESHOLD


def test_classify_verified_when_title_authors_and_venue_agree():
    ref = Reference(title="Attention Is All You Need", authors=["Vaswani", "Shazeer"], year=2017, venue="NeurIPS")
    recs = [_rec(title="Attention is all you need", authors=["Ashish Vaswani", "Noam Shazeer"], year=2017, venue="NeurIPS")]
    v = classify(ref, recs, sources_queried=["dblp"])
    assert v.status == STATUS_VERIFIED
    assert v.mismatches == []


def test_classify_unresolved_when_no_title_match():
    ref = Reference(title="A totally fabricated nonexistent paper title 9zq", authors=["Nobody"])
    recs = [_rec(title="Something entirely different about cells", authors=["Bio"])]
    v = classify(ref, recs, sources_queried=["crossref", "dblp"])
    assert v.status == STATUS_UNRESOLVED
    assert v.matched_record is None


def test_classify_metadata_mismatch_published_paper_cited_as_arxiv():
    # The core bug: title+authors match a DBLP ICLR record, but the citation
    # calls it an arXiv preprint. Must be metadata_mismatch (flagged with the
    # authoritative record), never unresolved/fabricated.
    ref = Reference(
        title="Some Real Published Paper", authors=["A. Author", "B. Coauthor"],
        venue="arXiv preprint arXiv:2401.01234", arxiv_id="2401.01234",
    )
    recs = [_rec(title="Some Real Published Paper", authors=["A. Author", "B. Coauthor"], venue="ICLR", year=2024)]
    v = classify(ref, recs, sources_queried=["dblp", "crossref"])
    assert v.status == STATUS_METADATA_MISMATCH
    assert v.matched_record.venue == "ICLR"
    assert any("venue" in m.lower() for m in v.mismatches)


def test_classify_metadata_mismatch_on_author_disagreement():
    ref = Reference(title="A Matching Title Here", authors=["Real", "Authors"])
    recs = [_rec(title="A Matching Title Here", authors=["Totally", "Different", "People"])]
    v = classify(ref, recs, sources_queried=["crossref"])
    assert v.status == STATUS_METADATA_MISMATCH
    assert any("author" in m.lower() for m in v.mismatches)


def test_classify_metadata_mismatch_on_identifier_conflict():
    ref = Reference(title="Paper With DOI", authors=["A"], doi="10.1/aaa")
    recs = [_rec(title="Paper With DOI", authors=["A"], doi="10.2/bbb")]
    v = classify(ref, recs, sources_queried=["crossref"])
    assert v.status == STATUS_METADATA_MISMATCH
    assert any("doi" in m.lower() or "identifier" in m.lower() for m in v.mismatches)


def test_classify_metadata_mismatch_on_year_disagreement():
    ref = Reference(title="A Stable Title For Year Test", authors=["A"], year=2019)
    recs = [_rec(title="A Stable Title For Year Test", authors=["A"], year=2024)]
    v = classify(ref, recs, sources_queried=["crossref"])
    assert v.status == STATUS_METADATA_MISMATCH
    assert any("year" in m.lower() for m in v.mismatches)


def test_classify_verified_when_year_within_tolerance():
    ref = Reference(title="A Stable Title For Year Test", authors=["A"], year=2022)
    recs = [_rec(title="A Stable Title For Year Test", authors=["A"], year=2023)]
    v = classify(ref, recs, sources_queried=["crossref"])
    assert v.status == STATUS_VERIFIED


def test_classify_verified_when_both_are_preprints():
    ref = Reference(title="A Preprint Only Work", authors=["A"],
                    venue="arXiv preprint arXiv:2401.00002", arxiv_id="2401.00002")
    recs = [_rec(title="A Preprint Only Work", authors=["A"], venue="arXiv", arxiv_id="2401.00002")]
    v = classify(ref, recs, sources_queried=["arxiv"])
    assert v.status == STATUS_VERIFIED


def test_classify_doi_prefix_forms_are_not_a_mismatch():
    ref = Reference(title="Paper With Prefixed DOI", authors=["A"], doi="doi:10.1145/3292500")
    recs = [_rec(title="Paper With Prefixed DOI", authors=["A"], doi="10.1145/3292500")]
    v = classify(ref, recs, sources_queried=["crossref"])
    assert v.status == STATUS_VERIFIED


# ---------------------------------------------------------------------------
# --- source adapters ---
# ---------------------------------------------------------------------------

def test_parse_crossref_extracts_record():
    payload = {"message": {"items": [{
        "title": ["Attention Is All You Need"],
        "author": [{"given": "Ashish", "family": "Vaswani"}, {"given": "Noam", "family": "Shazeer"}],
        "issued": {"date-parts": [[2017]]},
        "container-title": ["NeurIPS"],
        "DOI": "10.5555/abc",
        "URL": "https://doi.org/10.5555/abc",
    }]}}
    recs = parse_crossref(payload)
    assert recs and recs[0].source == "crossref"
    assert recs[0].title == "Attention Is All You Need"
    assert "Vaswani" in recs[0].authors[0]
    assert recs[0].year == 2017 and recs[0].venue == "NeurIPS"


def test_parse_openalex_extracts_record():
    payload = {"results": [{
        "title": "Some Paper",
        "publication_year": 2024,
        "authorships": [{"author": {"display_name": "Jane Roe"}}],
        "primary_location": {"source": {"display_name": "ICLR"}},
        "doi": "https://doi.org/10.1/x",
        "id": "https://openalex.org/W1",
    }]}
    recs = parse_openalex(payload)
    assert recs[0].source == "openalex" and recs[0].venue == "ICLR"
    assert recs[0].year == 2024 and recs[0].authors == ["Jane Roe"]
    assert recs[0].doi == "10.1/x"


def test_parse_semantic_scholar_extracts_record():
    payload = {"data": [{
        "title": "S2 Paper", "year": 2023,
        "authors": [{"name": "Al Pha"}, {"name": "Be Ta"}],
        "venue": "ACL",
        "externalIds": {"DOI": "10.9/z", "ArXiv": "2301.00001"},
        "url": "https://www.semanticscholar.org/p/1",
    }]}
    recs = parse_semantic_scholar(payload)
    assert recs[0].source == "s2" and recs[0].arxiv_id == "2301.00001"
    assert recs[0].doi == "10.9/z" and recs[0].venue == "ACL"


def test_parse_dblp_extracts_record():
    payload = {"result": {"hits": {"hit": [{"info": {
        "title": "DBLP Paper", "year": "2024", "venue": "ICLR",
        "authors": {"author": [{"text": "First Author"}, {"text": "Second Author"}]},
        "doi": "10.7/q", "url": "https://dblp.org/rec/1",
    }}]}}}
    recs = parse_dblp(payload)
    assert recs[0].source == "dblp" and recs[0].venue == "ICLR" and recs[0].year == 2024
    assert recs[0].authors == ["First Author", "Second Author"]


def test_parse_dblp_single_author_dict_and_list_venue():
    payload = {"result": {"hits": {"hit": [{"info": {
        "title": "Single Author Paper.",
        "year": "2024",
        "venue": ["ICLR", "ICLR Workshop"],
        "authors": {"author": {"text": "Solo Researcher"}},
        "url": "https://dblp.org/rec/2",
    }}]}}}
    recs = parse_dblp(payload)
    assert recs[0].authors == ["Solo Researcher"]          # single-author dict path
    assert recs[0].venue == "ICLR"                          # first of list, not stringified list
    assert recs[0].title == "Single Author Paper"           # trailing period stripped


def test_parse_arxiv_atom_extracts_record():
    atom = '''<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>ArXiv Paper Title</title>
        <author><name>Aa Bb</name></author>
        <author><name>Cc Dd</name></author>
        <published>2024-01-15T00:00:00Z</published>
        <id>http://arxiv.org/abs/2401.01234v2</id>
      </entry></feed>'''
    recs = parse_arxiv_atom(atom)
    assert recs[0].source == "arxiv" and recs[0].arxiv_id == "2401.01234"
    assert recs[0].year == 2024 and recs[0].authors == ["Aa Bb", "Cc Dd"]


# ---------------------------------------------------------------------------
# --- orchestrator + summary ---
# ---------------------------------------------------------------------------

def _fake_lookup_factory(records_by_key):
    """lookup(ref) -> (records, sources_queried), keyed by ref.key; empty if absent."""
    def _lookup(ref):
        return list(records_by_key.get(ref.key, [])), ["crossref", "dblp"]
    return _lookup


def test_resolve_references_classifies_all_and_summarizes():
    refs = [
        Reference(key="ok", title="Attention Is All You Need", authors=["Vaswani", "Shazeer"], venue="NeurIPS", year=2017),
        Reference(key="ghost", title="A Nonexistent Fabricated Title zzz9", authors=["Nobody"]),
    ]
    result = resolve_references(refs, lookup=_fake_lookup_factory({
        "ok": [_rec(title="Attention is all you need", authors=["Ashish Vaswani", "Noam Shazeer"], venue="NeurIPS", year=2017)],
    }))
    statuses = {v["key"]: v["status"] for v in result["verdicts"]}
    assert statuses["ok"] == STATUS_VERIFIED
    assert statuses["ghost"] == STATUS_UNRESOLVED
    assert result["summary"] == {"total": 2, "verified": 1, "metadata_mismatch": 0, "unresolved": 1}


def test_build_summary_counts_each_status():
    verdicts = [
        CitationVerdict(key="a", title="", status=STATUS_VERIFIED),
        CitationVerdict(key="b", title="", status=STATUS_METADATA_MISMATCH),
        CitationVerdict(key="c", title="", status=STATUS_UNRESOLVED),
        CitationVerdict(key="d", title="", status=STATUS_VERIFIED),
    ]
    assert build_summary(verdicts) == {"total": 4, "verified": 2, "metadata_mismatch": 1, "unresolved": 1}


# ---------------------------------------------------------------------------
# --- config integration ---
# ---------------------------------------------------------------------------

def test_check_citations_requires_paper(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="--check-citations requires --paper"):
        Config(repo_path=repo, output_dir=tmp_path / "out", run_citation_check=True)


def test_check_citations_paths_and_default_off(tmp_path):
    paper = tmp_path / "p.pdf"
    paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out", run_citation_check=True)
    assert cfg.run_citation_check is True
    assert cfg.citation_check_path.name == "citation_check.json"
    assert cfg.citation_check_path.parent.name == "evaluation"
    assert cfg.references_path.name == "references.json"
    # Default off when not requested.
    cfg2 = Config(paper_path=paper, output_dir=tmp_path / "out2")
    assert cfg2.run_citation_check is False


def test_citation_timeout_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("VERITAS_CITATION_TIMEOUT", "300")
    paper = tmp_path / "p.pdf"
    paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out")
    assert cfg.citation_timeout == 300


# ---------------------------------------------------------------------------
# --- citation-check prompt generation ---
# ---------------------------------------------------------------------------

from unittest.mock import patch

from veritas.core.runner import ReplicationRunner
from veritas.templates.prompt_generator import PromptGenerator


def test_citation_check_prompt_renders_key_instructions(tmp_path):
    gen = PromptGenerator()
    prompt = gen.generate_citation_check_prompt(
        output_dir=tmp_path / "out",
        paper_path=tmp_path / "paper.pdf",
        resolver_script_path=tmp_path / "out" / "evaluation" / "resolve_references.py",
    )
    # Reads the paper, writes references, runs the resolver, escalates unresolved,
    # writes the final JSON, and is forbidden from overriding resolver verdicts.
    assert "references.json" in prompt
    assert "resolve_references.py" in prompt
    assert "citation_check.json" in prompt
    assert "unresolved" in prompt
    assert "checked_support" in prompt
    # Anti-override discipline must be present.
    assert "do not override" in prompt.lower() or "authoritative" in prompt.lower()


# ---------------------------------------------------------------------------
# --- runner dispatch ---
# ---------------------------------------------------------------------------


def _citation_runner(tmp_path):
    paper = tmp_path / "paper.pdf"
    paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out", run_citation_check=True)
    runner = ReplicationRunner(cfg)
    cfg.evaluation_dir.mkdir(parents=True, exist_ok=True)
    return runner, cfg


def test_check_citations_stages_script_and_dispatches(tmp_path):
    runner, cfg = _citation_runner(tmp_path)

    def fake_invoke(prompt, working_dir, log_path, timeout=None, expose_api_keys=False):
        cfg.citation_check_path.write_text(
            '{"summary": {"total": 1, "verified": 1, "metadata_mismatch": 0, '
            '"unresolved": 0, "likely_fabricated": 0, "inconclusive": 0}, '
            '"flagged": [], "checked_support": false, "notes": "n"}',
            encoding="utf-8",
        )
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._check_citations()

    # The standalone resolver script was staged into the workspace.
    assert cfg.resolver_script_path.exists()
    assert "def classify" in cfg.resolver_script_path.read_text(encoding="utf-8")
    # Dispatched with default (key-stripped) env — never expose_api_keys.
    assert "expose_api_keys" not in m.call_args.kwargs  # never opt into keys for the citation agent
    assert cfg.citation_check_path.exists()


def test_check_citations_idempotent_skip(tmp_path):
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text('{"summary": {"total": 0}}', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._check_citations()
    m.assert_not_called()  # already produced -> skip


def test_check_citations_skips_cleanly_when_staging_fails(tmp_path):
    runner, cfg = _citation_runner(tmp_path)
    with patch.object(ReplicationRunner, "_stage_resolver_script", side_effect=OSError("disk full")), \
         patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._check_citations()  # must NOT raise
    m.assert_not_called()
    assert not cfg.citation_check_path.exists()


# ---------------------------------------------------------------------------
# --- report rendering ---
# ---------------------------------------------------------------------------

from veritas.core.report_generator import ReportGenerator


def test_render_citation_check_lists_flagged_and_summary(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 10, "verified": 7, "metadata_mismatch": 1, "unresolved": 0, "likely_fabricated": 1, "inconclusive": 1},
        "flagged": [
            {"key": "a2024", "raw": "A. Author. Fake paper. arXiv 2024.", "status": "likely_fabricated",
             "detail": "No dedicated page found for this title.", "matched_record": None,
             "evidence": ["https://www.google.com/search?q=..."]},
            {"key": "b2024", "raw": "B. Auth. Real paper. arXiv preprint.", "status": "metadata_mismatch",
             "detail": "cited as arXiv preprint but published at ICLR 2024 per DBLP",
             "matched_record": {"source": "dblp", "url": "https://dblp.org/x"}, "evidence": []},
        ],
        "checked_support": False,
        "notes": "support not checked",
    }), encoding="utf-8")

    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out))
    assert "## Citation Check" in section
    assert "likely fabricated" in section.lower()
    assert "a2024" in section and "b2024" in section
    assert "ICLR 2024" in section
    # Honest about what was not checked.
    assert "support" in section.lower()


def test_render_citation_check_empty_when_absent(tmp_path):
    gen = ReportGenerator()
    assert gen._render_citation_check(gen._load_citation_check(tmp_path / "nope")) == ""


def test_render_citation_check_clean_bill(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 5, "verified": 5, "metadata_mismatch": 0, "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0},
        "flagged": [], "checked_support": False, "notes": "n",
    }), encoding="utf-8")
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out))
    assert "## Citation Check" in section
    assert "all 5" in section.lower() or "no reference issues" in section.lower()


def test_render_citation_check_skips_malformed_flagged_entry(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 2, "verified": 1, "metadata_mismatch": 0,
                    "likely_fabricated": 1, "inconclusive": 0},
        "flagged": ["not a dict", {"key": "a2024", "status": "likely_fabricated",
                                   "detail": "missing", "matched_record": None, "evidence": []}],
        "checked_support": False,
    }), encoding="utf-8")
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out))  # must not raise
    assert "a2024" in section


def test_load_citation_check_tolerates_markdown_fences(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(
        "```json\n" + json.dumps({
            "summary": {"total": 1, "verified": 1, "metadata_mismatch": 0,
                        "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0},
            "flagged": [], "checked_support": False, "notes": "n",
        }) + "\n```\n",
        encoding="utf-8",
    )
    gen = ReportGenerator()
    data = gen._load_citation_check(out)
    assert data is not None and data["summary"]["total"] == 1


# ---------------------------------------------------------------------------
# Rich-record selection: _preferred_record drives classify
# ---------------------------------------------------------------------------

def test_classify_prefers_rich_record_and_flags_preprint_drift():
    # Same paper from two sources: a metadata-poor hit (openalex, empty venue)
    # and a rich one (dblp, NeurIPS). Cited as an arXiv preprint. The richer
    # record must win and the published-vs-preprint drift must be flagged.
    ref = Reference(title="Attention Is All You Need", authors=["Vaswani", "Shazeer"],
                    venue="arXiv preprint arXiv:1706.03762", arxiv_id="1706.03762")
    recs = [
        _rec(source="openalex", title="Attention Is All You Need",
             authors=["Ashish Vaswani", "Noam Shazeer"], venue="", year=2025),
        _rec(source="dblp", title="Attention is all you need",
             authors=["Ashish Vaswani", "Noam Shazeer"], venue="NeurIPS", year=2017),
    ]
    v = classify(ref, recs, sources_queried=["openalex", "dblp"])
    assert v.status == STATUS_METADATA_MISMATCH
    assert v.matched_record.source == "dblp"
    assert v.matched_record.venue == "NeurIPS"
    assert any("venue" in m.lower() for m in v.mismatches)


def test_classify_no_false_year_mismatch_from_metadata_poor_record():
    # A junk record (openalex, wrong year 2025) and a correct one (dblp, 2017)
    # for the same paper, citation correctly says 2017. The richer/correct record
    # must win so no false year mismatch is raised.
    ref = Reference(title="Attention Is All You Need", authors=["Vaswani", "Shazeer"],
                    venue="NeurIPS", year=2017)
    recs = [
        _rec(source="openalex", title="Attention Is All You Need",
             authors=["Ashish Vaswani", "Noam Shazeer"], venue="", year=2025),
        _rec(source="dblp", title="Attention is all you need",
             authors=["Ashish Vaswani", "Noam Shazeer"], venue="NeurIPS", year=2017),
    ]
    v = classify(ref, recs, sources_queried=["openalex", "dblp"])
    assert v.status == STATUS_VERIFIED
    assert v.matched_record.source == "dblp"


# ---------------------------------------------------------------------------
# --- faithfulness scope + audit paths ---
# ---------------------------------------------------------------------------

def test_faithfulness_scope_default_is_main(tmp_path):
    paper = tmp_path / "p.pdf"; paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out", run_citation_check=True)
    assert cfg.faithfulness_scope == "main"
    assert cfg.citation_audit_path.name == "citation_audit.json"
    assert cfg.citation_audit_path.parent.name == "evaluation"


def test_faithfulness_scope_explicit_all(tmp_path):
    paper = tmp_path / "p.pdf"; paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out",
                 run_citation_check=True, faithfulness_scope="all")
    assert cfg.faithfulness_scope == "all"


def test_faithfulness_scope_invalid_rejected(tmp_path):
    paper = tmp_path / "p.pdf"; paper.write_text("x")
    with pytest.raises(ValueError, match="faithfulness"):
        Config(paper_path=paper, output_dir=tmp_path / "out",
               run_citation_check=True, faithfulness_scope="bogus")


def test_faithfulness_scope_env_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("VERITAS_CITATION_FAITHFULNESS_SCOPE", "all")
    paper = tmp_path / "p.pdf"; paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out", run_citation_check=True)
    assert cfg.faithfulness_scope == "all"


# ---------------------------------------------------------------------------
# --- prompt generation (faithfulness + audit) ---
# ---------------------------------------------------------------------------

def test_citation_check_prompt_includes_faithfulness_main(tmp_path):
    gen = PromptGenerator()
    prompt = gen.generate_citation_check_prompt(
        output_dir=tmp_path / "out", paper_path=tmp_path / "paper.pdf",
        resolver_script_path=tmp_path / "out" / "evaluation" / "resolve_references.py",
        faithfulness_scope="main",
    )
    assert "faithfulness" in prompt.lower()
    assert "supported" in prompt and "partially_supported" in prompt
    assert "contradicted" in prompt and "not_mentioned" in prompt
    assert "verbatim quote" in prompt.lower()
    assert "central" in prompt.lower() or "main" in prompt.lower()


def test_citation_check_prompt_all_scope_changes_instruction(tmp_path):
    gen = PromptGenerator()
    p_all = gen.generate_citation_check_prompt(
        output_dir=tmp_path / "o", paper_path=tmp_path / "p.pdf",
        resolver_script_path=tmp_path / "o" / "evaluation" / "r.py",
        faithfulness_scope="all",
    )
    assert "every claim-bearing" in p_all.lower() or "all claim-bearing" in p_all.lower()


def test_citation_audit_prompt_renders(tmp_path):
    gen = PromptGenerator()
    prompt = gen.generate_citation_audit_prompt(
        output_dir=tmp_path / "out", paper_path=tmp_path / "paper.pdf",
    )
    assert "citation_check.json" in prompt
    assert "citation_audit.json" in prompt
    assert "independently" in prompt.lower() or "fresh" in prompt.lower()
    assert "human_review" in prompt


# --- runner: scope passing + audit dispatch ---

def _citation_runner_scope(tmp_path, scope="main"):
    paper = tmp_path / "paper.pdf"; paper.write_text("x")
    cfg = Config(paper_path=paper, output_dir=tmp_path / "out",
                 run_citation_check=True, faithfulness_scope=scope)
    runner = ReplicationRunner(cfg)
    cfg.evaluation_dir.mkdir(parents=True, exist_ok=True)
    return runner, cfg


def test_check_citations_passes_scope_to_prompt(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path, scope="all")
    seen = {}

    def fake_gen(output_dir, paper_path, resolver_script_path, faithfulness_scope="main"):
        seen["scope"] = faithfulness_scope
        return "PROMPT"

    def fake_invoke(prompt, working_dir, log_path, timeout=None, expose_api_keys=False):
        cfg.citation_check_path.write_text(
            '{"summary": {"total": 0, "verified": 0, "metadata_mismatch": 0, '
            '"unresolved": 0, "likely_fabricated": 0, "inconclusive": 0, '
            '"faithfulness": {"checked": 0, "supported": 0, "partially_supported": 0, '
            '"contradicted": 0, "not_mentioned": 0, "inaccessible": 0}, '
            '"faithfulness_scope": "all"}, "flagged": [], "faithfulness": [], '
            '"checked_support": true, "notes": ""}', encoding="utf-8")
        return True

    runner.prompt_generator.generate_citation_check_prompt = fake_gen
    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke), \
         patch.object(ReplicationRunner, "_audit_citations") as audit:
        runner._check_citations()
    assert seen["scope"] == "all"
    audit.assert_called_once()


def test_audit_citations_runs_and_writes(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path)
    cfg.citation_check_path.write_text(
        '{"summary": {}, "flagged": [{"key": "a", "status": "likely_fabricated"}], '
        '"faithfulness": []}', encoding="utf-8")

    def fake_invoke(prompt, working_dir, log_path, timeout=None, expose_api_keys=False):
        cfg.citation_audit_path.write_text(
            '{"audited_count": 1, "human_review": []}', encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._audit_citations()
    assert cfg.citation_audit_path.exists()
    assert "expose_api_keys" not in m.call_args.kwargs  # never opt into keys for the audit agent


def test_audit_citations_skips_when_nothing_flagged(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path)
    cfg.citation_check_path.write_text(
        '{"summary": {}, "flagged": [], "faithfulness": '
        '[{"key": "b", "verdict": "supported"}]}', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._audit_citations()
    m.assert_not_called()


def test_audit_citations_audits_contradicted_faithfulness(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path)
    cfg.citation_check_path.write_text(
        '{"summary": {}, "flagged": [], "faithfulness": '
        '[{"key": "c", "verdict": "contradicted"}]}', encoding="utf-8")
    def fake_invoke(prompt, working_dir, log_path, timeout=None, expose_api_keys=False):
        cfg.citation_audit_path.write_text('{"audited_count": 1, "human_review": []}', encoding="utf-8")
        return True
    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._audit_citations()
    m.assert_called_once()  # contradicted faithfulness is auditable


def test_audit_citations_never_raises_on_missing_check(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path)
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._audit_citations()  # must not raise
    m.assert_not_called()


def test_audit_citations_idempotent_skip(tmp_path):
    runner, cfg = _citation_runner_scope(tmp_path)
    cfg.citation_check_path.write_text(
        '{"summary": {}, "flagged": [{"key": "a", "status": "likely_fabricated"}], '
        '"faithfulness": []}', encoding="utf-8")
    cfg.citation_audit_path.write_text('{"audited_count": 0, "human_review": []}', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._audit_citations()
    m.assert_not_called()  # audit already produced -> skip
