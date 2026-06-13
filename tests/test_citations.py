"""Unit tests for the deterministic citation resolver (core/citations.py).

The HTTP layer is injected (a fake ``fetch_json``) so no network is touched.
Pure-function pieces (parsing, normalization, matching, classification) are
unit-tested directly, mirroring the pure-function test style of test_research.py.
"""
from __future__ import annotations

import json

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
# Task 3: best_match and classify
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
