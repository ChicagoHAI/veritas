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
    classify,
    STATUS_VERIFIED,
    STATUS_METADATA_MISMATCH,
    STATUS_UNRESOLVED,
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


def test_author_overlap_handles_surname_first_order():
    # APA-style "Surname, Initial." reference lists vs full-name records.
    assert author_overlap(["Vaswani, A."], ["Ashish Vaswani"]) == 1.0
    assert author_overlap(
        ["Vaswani, A.", "Shazeer, N."], ["Ashish Vaswani", "Noam Shazeer"]
    ) == 1.0
    # Compound surnames keep their final particle on both sides.
    assert author_overlap(["van der Berg, J."], ["Jan van der Berg"]) == 1.0
    # A genuinely different surname still fails to match.
    assert author_overlap(["Vaswani, A."], ["John Doe"]) == 0.0


def test_author_overlap_skips_generational_suffixes():
    # A suffix before the comma must not be mistaken for the surname.
    assert author_overlap(["Smith Jr., John"], ["John Smith"]) == 1.0
    # Suffixes are skipped on the record side too.
    assert author_overlap(["Smith Jr., John"], ["John Smith Jr."]) == 1.0
    assert author_overlap(["Davis III, R."], ["Richard Davis"]) == 1.0


def test_normalize_arxiv_id_strips_prefix_and_version():
    assert normalize_arxiv_id("arXiv:1706.03762v5") == "1706.03762"
    assert normalize_arxiv_id("1706.03762") == "1706.03762"
    assert normalize_arxiv_id("https://arxiv.org/abs/2401.01234") == "2401.01234"
    assert normalize_arxiv_id("10.1145/3292500") == ""  # a DOI, not an arXiv id


def test_normalize_arxiv_id_handles_old_style_ids():
    # Pre-2007 ids: archive(/subject-class)/YYMMNNN, optionally versioned.
    assert normalize_arxiv_id("hep-ph/9905221") == "hep-ph/9905221"
    assert normalize_arxiv_id("arXiv:hep-ph/9905221v2") == "hep-ph/9905221"
    assert normalize_arxiv_id("math.GT/0309136") == "math.GT/0309136"
    assert normalize_arxiv_id("https://arxiv.org/abs/hep-th/0603001") == "hep-th/0603001"


def test_author_overlap_ignores_et_al_and_folds_diacritics():
    # "et al." transcribed as an author entry must not count as an unmatched name.
    assert author_overlap(["A. Vaswani", "et al."], ["Ashish Vaswani", "Noam Shazeer"]) == 1.0
    assert author_overlap(["Smith, J.", "and others"], ["Jane Smith", "Bob Lee"]) == 1.0
    # Transliterated bibliography surnames match the record's accented form.
    assert author_overlap(["J. Muller"], ["Jürgen Müller"]) == 1.0


def test_normalize_title_folds_diacritics():
    assert normalize_title("Schrödinger's Cat") == "schrodinger s cat"


# ---------------------------------------------------------------------------
# --- record matching + verdict classification ---
# ---------------------------------------------------------------------------

def _rec(**kw):
    return SourceRecord(**{"source": "dblp", **kw})


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


def test_classify_no_year_mismatch_across_preprint_published_boundary():
    # A published citation matched only by a preprint record legitimately
    # differs in year (preprint predates the venue); the venue check owns
    # that comparison, so no year flag on a correct citation.
    ref = Reference(title="A Cross Boundary Work", authors=["A"], year=2019, venue="NeurIPS")
    recs = [_rec(source="arxiv", title="A Cross Boundary Work", authors=["A"],
                 year=2017, venue="arXiv")]
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


def test_parsers_coerce_string_years():
    # OpenAlex/S2 year fields are coerced like the other adapters, so classify's
    # numeric comparison can never see a string year.
    oa = parse_openalex({"results": [{"title": "T", "publication_year": "2020",
                                      "authorships": [], "primary_location": {}}]})
    assert oa[0].year == 2020
    s2 = parse_semantic_scholar({"data": [{"title": "T", "year": "2019",
                                           "authors": [], "externalIds": {}}]})
    assert s2[0].year == 2019


def test_fetchers_swallow_http_client_exceptions(monkeypatch):
    # IncompleteRead/BadStatusLine are HTTPException, not OSError; a mid-body
    # connection drop must degrade the source, not kill the resolver script.
    import http.client
    import urllib.request
    from veritas.core import citations as cit

    def boom(*args, **kwargs):
        raise http.client.IncompleteRead(b"partial")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert cit.fetch_json("https://example.org/api") is None
    assert cit.fetch_text("https://example.org/api") is None


def test_parse_dblp_extracts_record():
    payload = {"result": {"hits": {"hit": [{"info": {
        "title": "DBLP Paper", "year": "2024", "venue": "ICLR",
        "authors": {"author": [{"text": "First Author"}, {"text": "Second Author"}]},
        "doi": "10.7/q", "url": "https://dblp.org/rec/1",
    }}]}}}
    recs = parse_dblp(payload)
    assert recs[0].source == "dblp" and recs[0].venue == "ICLR" and recs[0].year == 2024
    assert recs[0].authors == ["First Author", "Second Author"]


def test_parse_dblp_single_hit_dict():
    # DBLP returns a bare dict for hits.hit when exactly one result matches —
    # the common case for a distinctive title. Must parse, not crash.
    payload = {"result": {"hits": {"hit": {"info": {
        "title": "Lone Hit Paper", "year": "2023", "venue": "NeurIPS",
        "authors": {"author": [{"text": "Only Author"}]},
        "url": "https://dblp.org/rec/3",
    }}}}}
    recs = parse_dblp(payload)
    assert len(recs) == 1
    assert recs[0].title == "Lone Hit Paper" and recs[0].year == 2023
    assert recs[0].authors == ["Only Author"]


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


def test_check_citations_reruns_over_malformed_output(tmp_path):
    # A truncated/invalid check output must not satisfy the resume gate, or
    # the feature goes permanently dead on it.
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text("{truncated", encoding="utf-8")

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_check_path.write_text('{"summary": {"total": 1}, "flagged": []}', encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._check_citations()
    assert m.called
    meta = json.loads(cfg.citation_check_meta_path.read_text(encoding="utf-8"))
    assert meta["faithfulness_scope"] == "main"


def test_check_citations_meta_only_stamped_for_valid_output(tmp_path):
    # A fresh run whose agent writes malformed JSON must not be stamped as
    # produced; the next invocation re-runs instead of resuming over it.
    runner, cfg = _citation_runner(tmp_path)

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_check_path.write_text("[1, 2", encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke):
        runner._check_citations()
    assert not cfg.citation_check_meta_path.exists()


def test_check_citations_reruns_on_corrupt_meta(tmp_path):
    # A meta sidecar that exists but cannot be parsed is damaged tracking
    # data: re-run rather than trust output of unknown scope.
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text('{"summary": {"total": 0}, "flagged": []}', encoding="utf-8")
    cfg.citation_check_meta_path.write_text("{truncated", encoding="utf-8")

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_check_path.write_text('{"summary": {"total": 0}, "flagged": []}', encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._check_citations()
    assert m.called


def test_check_citations_existing_fails_when_check_produces_nothing(tmp_path):
    # The standalone subcommand's whole purpose is the check; a run where it
    # produced nothing must exit non-zero and leave the report untouched.
    runner, cfg = _citation_runner(tmp_path)
    with patch.object(ReplicationRunner, "_invoke_provider", return_value=False), \
         patch.object(runner, "report_generator") as rg:
        result = runner.check_citations_existing()
    assert not result.success
    rg.generate.assert_not_called()


def test_check_citations_skip_path_still_runs_missing_audit(tmp_path):
    # A prior run's check succeeded but its audit didn't (e.g. interrupted).
    # The idempotency fast-path must still give the audit its pass instead of
    # returning before it.
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text(json.dumps({
        "summary": {"total": 1},
        "flagged": [{"key": "f2024", "status": "likely_fabricated", "detail": "d",
                     "matched_record": None, "evidence": []}],
    }), encoding="utf-8")

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_audit_path.write_text('{"audited_count": 1, "items": []}', encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._check_citations()

    assert m.call_count == 1  # the audit dispatch, not a re-run of the check
    assert cfg.citation_audit_path.exists()


def test_check_citations_skips_when_scope_matches_meta(tmp_path):
    runner, cfg = _citation_runner(tmp_path)  # default scope: main
    cfg.citation_check_path.write_text('{"summary": {"total": 0}}', encoding="utf-8")
    cfg.citation_check_meta_path.write_text('{"faithfulness_scope": "main"}', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._check_citations()
    m.assert_not_called()


def test_check_citations_reruns_on_scope_change(tmp_path):
    runner, cfg = _citation_runner(tmp_path)  # current scope: main
    cfg.citation_check_path.write_text('{"summary": {"total": 0}}', encoding="utf-8")
    cfg.citation_check_meta_path.write_text('{"faithfulness_scope": "all"}', encoding="utf-8")
    cfg.citation_audit_path.write_text('{"items": []}', encoding="utf-8")  # stale audit

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_check_path.write_text(
            '{"summary": {"total": 1, "verified": 1}, "flagged": []}', encoding="utf-8"
        )
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke) as m:
        runner._check_citations()

    assert m.called  # scope changed -> re-dispatched
    assert not cfg.citation_audit_path.exists()  # stale audit invalidated
    meta = json.loads(cfg.citation_check_meta_path.read_text(encoding="utf-8"))
    assert meta["faithfulness_scope"] == "main"  # meta re-recorded for the new run


def test_check_citations_scope_rerun_never_mislabels_stale_output(tmp_path):
    # Provider success is only the exit code. If the re-run exits cleanly but
    # never writes the file, the old-scope output must not survive to be
    # stamped as produced by the new scope.
    runner, cfg = _citation_runner(tmp_path)  # current scope: main
    cfg.citation_check_path.write_text('{"summary": {"total": 7}}', encoding="utf-8")
    cfg.citation_check_meta_path.write_text('{"faithfulness_scope": "all"}', encoding="utf-8")

    with patch.object(ReplicationRunner, "_invoke_provider", return_value=True):
        runner._check_citations()

    assert not cfg.citation_check_path.exists()  # stale output cleared, not re-served
    assert not cfg.citation_check_meta_path.exists()  # no meta claiming a fresh main-scope run


def test_check_citations_skips_cleanly_when_staging_fails(tmp_path):
    runner, cfg = _citation_runner(tmp_path)
    with patch.object(ReplicationRunner, "_stage_resolver_script", side_effect=OSError("disk full")), \
         patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._check_citations()  # must NOT raise
    m.assert_not_called()
    assert not cfg.citation_check_path.exists()


def test_check_citations_tolerates_non_object_json_output(tmp_path):
    # Valid JSON that is not an object (e.g. `[]`) must not raise into run().
    runner, cfg = _citation_runner(tmp_path)

    def fake_invoke(prompt, working_dir, log_path, timeout=None):
        cfg.citation_check_path.write_text("[]", encoding="utf-8")
        return True

    with patch.object(ReplicationRunner, "_invoke_provider", side_effect=fake_invoke):
        runner._check_citations()  # must NOT raise


def test_audit_citations_tolerates_non_object_check_json(tmp_path):
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text('["not an object"]', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._audit_citations()  # must NOT raise
    m.assert_not_called()


def test_check_citations_existing_sanitizes_logs(tmp_path):
    # The standalone entry point never goes through run(), so it must apply
    # the API-key redaction pass itself.
    runner, cfg = _citation_runner(tmp_path)
    cfg.citation_check_path.write_text('{"summary": {"total": 0}}', encoding="utf-8")
    with patch("veritas.core.runner.sanitize_logs_directory") as san, \
         patch.object(runner, "report_generator") as rg:
        rg.generate.return_value = (None, None)
        result = runner.check_citations_existing()
    assert result.success
    san.assert_called_once_with(cfg.output_dir)


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


def test_soften_verdict_unresolved_can_soften_to_inconclusive():
    final, softened = ReportGenerator._soften_verdict("unresolved", "inconclusive", "integrity")
    assert final == "inconclusive" and softened


def test_soften_verdict_ignores_inaccessible_audit():
    # "inaccessible" carries no information: keep the first verdict, unsoftened.
    final, softened = ReportGenerator._soften_verdict("contradicted", "inaccessible", "faithfulness")
    assert final == "contradicted" and not softened


def test_render_citation_check_counts_unresolved_and_annotates_inaccessible_audit(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 4, "verified": 2, "metadata_mismatch": 0, "unresolved": 1,
                    "likely_fabricated": 0, "inconclusive": 1,
                    "faithfulness": {"checked": 1, "supported": 0, "partially_supported": 0,
                                     "contradicted": 1, "not_mentioned": 0, "inaccessible": 0},
                    "faithfulness_scope": "main"},
        "flagged": [
            {"key": "u2024", "status": "unresolved", "detail": "web search did not complete",
             "matched_record": None, "evidence": []},
            {"key": "i2024", "status": "inconclusive", "detail": "found but not indexed",
             "matched_record": None, "evidence": []},
        ],
        "faithfulness": [
            {"key": "c2024", "claim": "X causes Y", "source_status": "retrieved",
             "verdict": "contradicted", "quote": "X does not cause Y",
             "source": "https://example.org/c2024", "detail": "d"},
        ],
        "checked_support": True,
    }), encoding="utf-8")
    (out / "evaluation" / "citation_audit.json").write_text(json.dumps({
        "audited_count": 1,
        "items": [{"key": "c2024", "kind": "faithfulness",
                   "audit_verdict": "inaccessible", "note": "paywalled"}],
    }), encoding="utf-8")

    gen = ReportGenerator()
    section = gen._render_citation_check(
        gen._load_citation_check(out), gen._load_citation_audit(out))
    assert "1 unresolved" in section          # summary line counts it
    assert "| unresolved |" in section        # flagged table renders it
    # An inaccessible audit keeps the verdict but is annotated, not silent.
    assert "contradicted (audit could not retrieve the source)" in section
    assert "audit softened" not in section


def test_audit_softening_is_claim_specific():
    # Two distinct claims cite the same reference key; an audit item that
    # names one claim must soften only that row.
    citation = {
        "summary": {"total": 1, "verified": 1, "metadata_mismatch": 0,
                    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
                    "faithfulness": {"checked": 2, "contradicted": 2,
                                     "partially_supported": 0}},
        "flagged": [],
        "faithfulness": [
            {"key": "same2024", "claim": "claim one text",
             "verdict": "contradicted", "quote": "q1", "source_status": "retrieved"},
            {"key": "same2024", "claim": "claim two text",
             "verdict": "contradicted", "quote": "q2", "source_status": "retrieved"},
        ],
    }
    audit = {
        "audited_count": 1,
        "items": [
            {"key": "same2024", "kind": "faithfulness", "claim": "claim one text",
             "audit_verdict": "supported", "note": "holds up"},
        ],
    }
    section = ReportGenerator()._render_citation_check(citation, audit)
    assert section.count("audit softened from contradicted") == 1


def test_audit_without_claim_field_still_softens():
    # Audits from before claim tracking carry no claim field; their verdict
    # applies by (key, kind) as before.
    citation = {
        "summary": {"total": 1, "verified": 1, "metadata_mismatch": 0,
                    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
                    "faithfulness": {"checked": 1, "contradicted": 1,
                                     "partially_supported": 0}},
        "flagged": [],
        "faithfulness": [
            {"key": "solo2024", "claim": "only claim",
             "verdict": "contradicted", "quote": "q", "source_status": "retrieved"},
        ],
    }
    audit = {
        "audited_count": 1,
        "items": [
            {"key": "solo2024", "kind": "faithfulness",
             "audit_verdict": "supported", "note": "holds up"},
        ],
    }
    section = ReportGenerator()._render_citation_check(citation, audit)
    assert "audit softened from contradicted" in section


def test_audit_claim_match_tolerates_whitespace_and_case_drift():
    citation = {
        "summary": {"total": 1, "verified": 1, "metadata_mismatch": 0,
                    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
                    "faithfulness": {"checked": 1, "contradicted": 1,
                                     "partially_supported": 0}},
        "flagged": [],
        "faithfulness": [
            {"key": "drift2024", "claim": "The method improves accuracy",
             "verdict": "contradicted", "quote": "q", "source_status": "retrieved"},
        ],
    }
    audit = {
        "audited_count": 1,
        "items": [
            {"key": "drift2024", "kind": "faithfulness",
             "claim": "  the method  improves\naccuracy ",
             "audit_verdict": "supported", "note": "holds"},
        ],
    }
    section = ReportGenerator()._render_citation_check(citation, audit)
    assert "audit softened from contradicted" in section


def test_audit_non_string_claim_does_not_crash():
    citation = {
        "summary": {"total": 1, "verified": 1, "metadata_mismatch": 0,
                    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
                    "faithfulness": {"checked": 1, "contradicted": 1,
                                     "partially_supported": 0}},
        "flagged": [],
        "faithfulness": [
            {"key": "odd2024", "claim": ["not", "a", "string"],
             "verdict": "contradicted", "quote": "q", "source_status": "retrieved"},
        ],
    }
    audit = {
        "audited_count": 1,
        "items": [
            {"key": "odd2024", "kind": "faithfulness", "claim": 42,
             "audit_verdict": "supported", "note": "holds"},
        ],
    }
    section = ReportGenerator()._render_citation_check(citation, audit)
    assert "Citation Check" in section


def test_citation_html_view_mirrors_markdown_reconciliation(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 3, "verified": 1, "metadata_mismatch": 1, "unresolved": 0,
                    "likely_fabricated": 1, "inconclusive": 0,
                    "faithfulness": {"checked": 1, "supported": 1, "partially_supported": 0,
                                     "contradicted": 0, "not_mentioned": 0, "inaccessible": 0},
                    "faithfulness_scope": "main"},
        "flagged": [
            {"key": "f2024", "status": "likely_fabricated", "detail": "no dedicated page",
             "matched_record": None, "evidence": ["https://example.org/search"]},
            {"key": "m2024", "status": "metadata_mismatch", "detail": "published at ICLR",
             "matched_record": {"source": "dblp", "url": "https://dblp.org/x"}, "evidence": []},
        ],
        "faithfulness": [
            {"key": "s2024", "claim": "X improves Y", "source_status": "retrieved",
             "verdict": "supported", "quote": "X improves Y", "source": "https://example.org/s"},
        ],
        "checked_support": True,
    }), encoding="utf-8")
    (out / "evaluation" / "citation_audit.json").write_text(json.dumps({
        "audited_count": 1,
        "items": [{"key": "f2024", "kind": "integrity", "audit_verdict": "inconclusive", "note": "found a page"}],
    }), encoding="utf-8")

    gen = ReportGenerator()
    view = gen._citation_html_view(gen._load_citation_check(out), gen._load_citation_audit(out))
    assert view["total"] == 3 and view["fabricated"] == 1
    by_key = {r["key"]: r for r in view["flagged"]}
    # The audit's milder verdict softened the fabrication flag, same as the md path.
    assert by_key["f2024"]["status_label"] == "inconclusive (audit softened from likely_fabricated)"
    assert by_key["m2024"]["url"] == "https://dblp.org/x"
    assert view["softened_count"] == 1
    assert view["faith_rows"][0]["verdict_label"] == "supported"


def test_citation_html_view_none_when_check_absent(tmp_path):
    gen = ReportGenerator()
    assert gen._citation_html_view(None, None) is None


def test_citation_html_view_normalizes_whitespace_keys():
    # Same key normalization as the markdown path: whitespace-only -> "?".
    gen = ReportGenerator()
    view = gen._citation_html_view({
        "summary": {"total": 1},
        "flagged": [{"key": "   ", "status": "inconclusive", "detail": "d",
                     "matched_record": None, "evidence": []}],
        "checked_support": False,
    }, None)
    assert view["flagged"][0]["key"] == "?"
    assert view["support_not_checked"] is True


def test_html_report_renders_citation_section(tmp_path):
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 2, "verified": 1, "metadata_mismatch": 0, "unresolved": 0,
                    "likely_fabricated": 1, "inconclusive": 0},
        "flagged": [{"key": "f2024", "status": "likely_fabricated",
                     "detail": "no dedicated page", "matched_record": None, "evidence": []}],
        "checked_support": False,
    }), encoding="utf-8")

    gen = ReportGenerator()
    ctx = gen._build_html_context(
        None, [], None, None, None, None, "full",
        citation=gen._load_citation_check(out),
        citation_audit=gen._load_citation_audit(out),
    )
    html = gen._render_html(ctx)
    assert "Citation check" in html
    assert "likely fabricated" in html
    assert "f2024" in html
    # checked_support is False in the fixture -> same disclaimer as the md report.
    assert "does not check citation support" in html
    # Absent check -> no section.
    ctx_none = gen._build_html_context(None, [], None, None, None, None, "full")
    assert "Citation check" not in gen._render_html(ctx_none)


def test_renderers_tolerate_hostile_agent_json():
    # Every string field the wrong type, plus unsafe URL schemes: the report
    # must degrade field-by-field, never crash, and never emit non-http links.
    citation = {
        "summary": {"total": 2, "verified": 0, "metadata_mismatch": 0, "unresolved": 0,
                    "likely_fabricated": 2, "inconclusive": 0,
                    "faithfulness": {"checked": 1, "contradicted": 1}},
        "flagged": [
            {"key": 12, "status": 7, "detail": 42, "matched_record": "not a dict",
             "evidence": {"not": "a list"}},
            {"key": "js2024", "status": "likely_fabricated", "detail": "d",
             "matched_record": {"source": "dblp", "url": "javascript:alert(1)"},
             "evidence": ["javascript:alert(2)"]},
        ],
        "faithfulness": [
            {"key": None, "claim": ["not", "a", "string"], "verdict": 3, "quote": 9,
             "source": "javascript:alert(3)", "source_status": "retrieved"},
        ],
        "checked_support": False,
    }
    audit = {"items": [
        {"key": ["unhashable"], "kind": "integrity", "audit_verdict": []},
        {"key": "js2024", "kind": "integrity", "audit_verdict": "inconclusive"},
    ]}

    gen = ReportGenerator()
    md = gen._render_citation_check(citation, audit)  # must not raise
    assert "js2024" in md
    assert "javascript:" not in md
    view = gen._citation_html_view(citation, audit)   # must not raise
    by_key = {r["key"]: r for r in view["flagged"]}
    assert by_key["js2024"]["url"] == ""              # unsafe scheme never linked
    html = gen._render_html(gen._build_html_context(
        None, [], None, None, None, None, "full",
        citation=citation, citation_audit=audit))
    assert "javascript:" not in html


def test_renderers_tolerate_non_dict_summary():
    citation = {"summary": ["oops"], "flagged": [], "checked_support": True}
    gen = ReportGenerator()
    assert "Citation Check" in gen._render_citation_check(citation, None)
    assert gen._citation_html_view(citation, None)["total"] == 0


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
    assert "items" in prompt and "audit_verdict" in prompt


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
            '{"audited_count": 1, "items": []}', encoding="utf-8")
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
        cfg.citation_audit_path.write_text('{"audited_count": 1, "items": []}', encoding="utf-8")
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
    cfg.citation_audit_path.write_text('{"audited_count": 0, "items": []}', encoding="utf-8")
    with patch.object(ReplicationRunner, "_invoke_provider") as m:
        runner._audit_citations()
    m.assert_not_called()  # audit already produced -> skip


# ---------------------------------------------------------------------------
# --- report: faithfulness + human-review rendering ---
# ---------------------------------------------------------------------------

def _write_check(out, faithfulness, summary_extra=None):
    (out / "evaluation").mkdir(parents=True, exist_ok=True)
    fsum = summary_extra or {
        "checked": len(faithfulness), "supported": 0, "partially_supported": 0,
        "contradicted": 0, "not_mentioned": 0, "inaccessible": 0,
    }
    base = {"summary": {"total": 3, "verified": 3, "metadata_mismatch": 0,
                        "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
                        "faithfulness": fsum, "faithfulness_scope": "main"},
            "flagged": [], "faithfulness": faithfulness, "checked_support": True, "notes": ""}
    (out / "evaluation" / "citation_check.json").write_text(json.dumps(base), encoding="utf-8")


def test_render_faithfulness_section_lists_checked_claims(tmp_path):
    out = tmp_path / "out"
    _write_check(out, [
        {"key": "a2023", "claim": "A causes B", "source_status": "retrieved",
         "verdict": "partially_supported", "quote": "A is associated with B",
         "evidence_basis": "fetched_full", "source": "https://x", "detail": "over-claimed"},
    ], summary_extra={"checked": 1, "supported": 0, "partially_supported": 1,
                      "contradicted": 0, "not_mentioned": 0, "inaccessible": 0})
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out))
    assert "Citation Check" in section
    assert "claim support" in section.lower() or "faithfulness" in section.lower()
    assert "a2023" in section and "partially supported" in section.lower()
    assert "A is associated with B" in section  # the quote is shown


def test_render_citation_check_omits_review_section(tmp_path):
    out = tmp_path / "out"
    _write_check(out, [])
    gen = ReportGenerator()
    section = gen._render_citation_check(
        gen._load_citation_check(out), gen._load_citation_audit(out))
    assert "human review" not in section.lower()


def test_render_citation_check_still_renders_without_faithfulness(tmp_path):
    # backward-safe: an old-style file with no faithfulness still renders the integrity part
    out = tmp_path / "out"
    (out / "evaluation").mkdir(parents=True)
    (out / "evaluation" / "citation_check.json").write_text(json.dumps({
        "summary": {"total": 2, "verified": 2, "metadata_mismatch": 0, "unresolved": 0,
                    "likely_fabricated": 0, "inconclusive": 0},
        "flagged": [], "checked_support": False, "notes": "n"}), encoding="utf-8")
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out), gen._load_citation_audit(out))
    assert "Citation Check" in section  # no crash, integrity still renders


def _write_audit(out, items):
    (out / "evaluation").mkdir(parents=True, exist_ok=True)
    (out / "evaluation" / "citation_audit.json").write_text(
        json.dumps({"audited_count": len(items), "items": items}), encoding="utf-8")


def test_soften_verdict_only_downgrades():
    gen = ReportGenerator()
    # integrity: audit milder -> soften
    assert gen._soften_verdict("likely_fabricated", "inconclusive", "integrity") == ("inconclusive", True)
    assert gen._soften_verdict("metadata_mismatch", "verified", "integrity") == ("verified", True)
    # never escalate
    assert gen._soften_verdict("verified", "likely_fabricated", "integrity") == ("verified", False)
    # agree -> no change
    assert gen._soften_verdict("metadata_mismatch", "metadata_mismatch", "integrity") == ("metadata_mismatch", False)
    # faithfulness: audit milder -> soften
    assert gen._soften_verdict("contradicted", "partially_supported", "faithfulness") == ("partially_supported", True)
    # no/unknown audit verdict -> keep first
    assert gen._soften_verdict("contradicted", None, "faithfulness") == ("contradicted", False)
    assert gen._soften_verdict("contradicted", "inaccessible", "faithfulness") == ("contradicted", False)


def test_render_faithfulness_softened_by_audit(tmp_path):
    out = tmp_path / "out"
    _write_check(out, [
        {"key": "y2023", "claim": "A causes B", "source_status": "retrieved",
         "verdict": "contradicted", "quote": "A is associated with B",
         "evidence_basis": "fetched_full", "source": "https://x", "detail": "d"},
    ], summary_extra={"checked": 1, "supported": 0, "partially_supported": 0,
                      "contradicted": 1, "not_mentioned": 0, "inaccessible": 0})
    _write_audit(out, [{"key": "y2023", "kind": "faithfulness",
                        "audit_verdict": "partially_supported", "note": "partly backs it"}])
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out), gen._load_citation_audit(out))
    assert "partially supported" in section.lower()
    assert "softened" in section.lower()
    assert "human review" not in section.lower()


def test_render_no_audit_keeps_verify_verdict(tmp_path):
    out = tmp_path / "out"
    _write_check(out, [
        {"key": "y2023", "claim": "c", "source_status": "retrieved", "verdict": "contradicted",
         "quote": "q", "evidence_basis": "fetched_full", "source": "https://x", "detail": "d"},
    ], summary_extra={"checked": 1, "supported": 0, "partially_supported": 0,
                      "contradicted": 1, "not_mentioned": 0, "inaccessible": 0})
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out), gen._load_citation_audit(out))
    assert "contradicted" in section.lower()
    assert "softened" not in section.lower()


def test_render_faithfulness_skips_non_dict_entries(tmp_path):
    out = tmp_path / "out"
    _write_check(out, ["not a dict", {"key": "ok", "source_status": "retrieved",
                                      "verdict": "supported", "quote": "q", "claim": "c",
                                      "source": "https://s"}],
                 summary_extra={"checked": 2, "supported": 1, "partially_supported": 0,
                                "contradicted": 0, "not_mentioned": 0, "inaccessible": 0})
    gen = ReportGenerator()
    section = gen._render_citation_check(gen._load_citation_check(out))  # must not raise
    assert "ok" in section


# ---------------------------------------------------------------------------
# --- standalone check_citations_existing ---
# ---------------------------------------------------------------------------


def test_check_citations_existing_runs_check_and_report(tmp_path):
    paper = tmp_path / "paper.pdf"; paper.write_text("x")
    out = tmp_path / "out"
    cfg = Config(paper_path=paper, output_dir=out, run_citation_check=True)
    runner = ReplicationRunner(cfg)
    calls = {"check": 0, "report": 0}

    def fake_check():
        calls["check"] += 1
        cfg.evaluation_dir.mkdir(parents=True, exist_ok=True)
        cfg.citation_check_path.write_text(
            '{"summary": {"total": 0}, "flagged": []}', encoding="utf-8"
        )

    def fake_generate(replicate_dir, output_path=None, generate_pdf=True, generate_md=True):
        calls["report"] += 1
        return (out / "report" / "r.md", None)

    with patch.object(ReplicationRunner, "_check_citations", side_effect=fake_check):
        runner.report_generator.generate = fake_generate
        result = runner.check_citations_existing()
    assert calls["check"] == 1 and calls["report"] == 1
    assert result.success
    assert result.report_path == out / "report" / "r.md"
