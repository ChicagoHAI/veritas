"""Deterministic grader — shape handling (regression for the C1 dict-scalar bug)."""

from veritas.core.grading import grade_claim


def test_scalar_with_flat_dict_values_grades_per_key():
    # Regression: a claim typed "scalar" whose value is a flat dict (e.g. the
    # Cooperation paper's C1 = {"N50": 0.69, "N200": 0.65}) was bailing to
    # not_attempted because the scalar grader couldn't read a dict. It should
    # grade per key like a table instead.
    structured = {
        "replicated_value": {"N50": 0.69, "N200": 0.66},
        "paper_value": {"N50": 0.69, "N200": 0.65},
        "value_found": True,
    }
    status, _why, graded_by = grade_claim("scalar", structured, proposed_status="match")
    assert status == "match"
    assert graded_by == "deterministic"


def test_scalar_with_flat_dict_one_bad_cell_is_partial():
    structured = {
        "replicated_value": {"a": 1.0, "b": 9.0},
        "paper_value": {"a": 1.0, "b": 2.0},
        "value_found": True,
    }
    assert grade_claim("scalar", structured, "match")[0] == "partial"


def test_scalar_with_flat_dict_missing_key_is_no_match_cell():
    structured = {
        "replicated_value": {"a": 1.0},          # missing "b"
        "paper_value": {"a": 1.0, "b": 2.0},
        "value_found": True,
    }
    # one match cell + one missing(=no_match) cell -> partial
    assert grade_claim("scalar", structured, "match")[0] == "partial"


def test_plain_scalar_still_works():
    assert grade_claim("scalar", {"replicated_value": 0.42, "paper_value": 0.41,
                                  "value_found": True}, "match")[0] == "match"
    assert grade_claim("scalar", {"replicated_value": 5.0, "paper_value": 1.0,
                                  "value_found": True}, "match")[0] == "no_match"


def test_scalar_value_not_found_is_not_attempted():
    assert grade_claim("scalar", {"value_found": False}, "match")[0] == "not_attempted"
