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
