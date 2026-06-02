"""Deterministic, LLM-free grading of replicated values against paper values.

The verify phase is split into two roles (see
``notes/2026-06-01-verifier-split-design.md``):

- the **comparator** (an LLM, in ``templates/verify/single_claim.md``) reads the
  messy replication evidence and emits the *objective* replicated value in a
  normalized structured form — including a numeric ``uncertainty`` when the claim
  conveys one. It does not decide the numeric verdict.
- the **grader** (this module — pure functions, no I/O, no model) decides
  ``match | partial | no_match | not_attempted`` from those normalized numbers
  against the paper value and a declared tolerance.

This makes numeric grading reproducible and auditable: a number plus a rule, not
a prompt. Only deterministically-decidable claim types are graded here; for
``qualitative`` / ``figure`` the comparator's own LLM judgment is kept
(passthrough), since there is no number to compute on. That routing is the
caller's job (``runner._run_single_verify``); this module exposes
``DETERMINISTIC_TYPES`` so the caller knows which.
"""

from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

# Claim types this module grades deterministically. Others (qualitative,
# figure) keep the comparator's LLM judgment.
DETERMINISTIC_TYPES = frozenset({"scalar", "scalar_range", "table"})


@dataclass(frozen=True)
class GradingTolerances:
    """Tolerance thresholds for deterministic grading (issue #56).

    Defaults mirror the rules previously described in prose in
    ``templates/verify/single_claim.md``. Lifted into a config object so a run
    is reproducible from its tolerances and they can be tuned per domain.
    """
    match_rel: float = 0.05        # |rel err| <= this -> match (no uncertainty)
    partial_rel: float = 0.30      # |rel err| <= this -> partial
    sigma_match: float = 1.0       # within +/- this many sigma -> match
    sigma_partial: float = 2.0     # within +/- this many sigma -> partial
    near_zero_abs: float = 1e-9    # |paper| below this -> use absolute compare
    match_abs: float = 1e-6        # absolute match band when paper value ~ 0
    range_overlap_match: float = 0.80  # range overlap fraction for a table/range match

    def to_dict(self) -> dict:
        return {
            "match_rel": self.match_rel,
            "partial_rel": self.partial_rel,
            "sigma_match": self.sigma_match,
            "sigma_partial": self.sigma_partial,
            "near_zero_abs": self.near_zero_abs,
            "match_abs": self.match_abs,
            "range_overlap_match": self.range_overlap_match,
        }


# Result of grading: (status, rationale). status is one of the verdict strings.
GradeResult = Tuple[str, str]


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _as_number_list(x: Any) -> Optional[List[float]]:
    """Coerce a scalar or flat list of scalars to a list of floats; else None."""
    if _is_number(x):
        return [float(x)]
    if isinstance(x, (list, tuple)) and x and all(_is_number(v) for v in x):
        return [float(v) for v in x]
    return None


def _grade_one_scalar(
    rep: float,
    paper: float,
    uncertainty: Optional[float],
    tol: GradingTolerances,
) -> Tuple[str, str]:
    """Grade a single scalar against a single paper value."""
    diff = abs(rep - paper)
    if uncertainty is not None and _is_number(uncertainty) and uncertainty > 0:
        n_sigma = diff / uncertainty
        if n_sigma <= tol.sigma_match:
            return "match", f"|{rep:g}-{paper:g}|={diff:g} = {n_sigma:.2g}σ ≤ {tol.sigma_match:g}σ → match"
        if n_sigma <= tol.sigma_partial:
            return "partial", f"{n_sigma:.2g}σ ≤ {tol.sigma_partial:g}σ → partial"
        return "no_match", f"{n_sigma:.2g}σ > {tol.sigma_partial:g}σ → no_match"
    # No uncertainty: relative error, with an absolute band for near-zero paper.
    if abs(paper) < tol.near_zero_abs:
        if diff <= tol.match_abs:
            return "match", f"paper≈0; |Δ|={diff:g} ≤ {tol.match_abs:g} → match"
        return "no_match", f"paper≈0; |Δ|={diff:g} > {tol.match_abs:g} → no_match"
    rel = diff / abs(paper)
    if rel <= tol.match_rel:
        return "match", f"rel err {rel:.2%} ≤ {tol.match_rel:.0%} → match"
    if rel <= tol.partial_rel:
        return "partial", f"rel err {rel:.2%} ≤ {tol.partial_rel:.0%} → partial"
    return "no_match", f"rel err {rel:.2%} > {tol.partial_rel:.0%} → no_match"


def _aggregate(statuses: List[str]) -> str:
    """Combine per-element statuses into one: all-match→match, all-no→no_match,
    anything mixed (or any partial) → partial."""
    if not statuses:
        return "not_attempted"
    if all(s == "match" for s in statuses):
        return "match"
    if all(s == "no_match" for s in statuses):
        return "no_match"
    return "partial"


def _grade_scalar(structured: dict, tol: GradingTolerances) -> GradeResult:
    if not structured.get("value_found", True):
        return "not_attempted", "comparator reported no replicated value was produced"
    rep = structured.get("replicated_value")
    paper = structured.get("paper_value")
    rep_list = _as_number_list(rep)
    paper_list = _as_number_list(paper)
    if rep_list is None or paper_list is None:
        return "not_attempted", "replicated or paper value is missing / non-numeric"
    if len(rep_list) != len(paper_list):
        return "no_match", f"shape mismatch: {len(rep_list)} replicated vs {len(paper_list)} paper values"
    unc = structured.get("uncertainty")
    unc_list = _as_number_list(unc) if unc is not None else None
    statuses, notes = [], []
    for i, (r, p) in enumerate(zip(rep_list, paper_list)):
        u = None
        if unc_list is not None:
            u = unc_list[i] if len(unc_list) == len(rep_list) else unc_list[0]
        s, why = _grade_one_scalar(r, p, u, tol)
        statuses.append(s)
        notes.append(why if len(rep_list) == 1 else f"[{i}] {why}")
    return _aggregate(statuses), "; ".join(notes)


def _grade_scalar_range(structured: dict, tol: GradingTolerances) -> GradeResult:
    if not structured.get("value_found", True):
        return "not_attempted", "comparator reported no replicated value was produced"
    rep_list = _as_number_list(structured.get("replicated_value"))
    rng = structured.get("paper_range")
    if rep_list is None or not (isinstance(rng, (list, tuple)) and len(rng) == 2 and all(_is_number(v) for v in rng)):
        return "not_attempted", "replicated value or paper_range missing / malformed"
    low, high = float(min(rng)), float(max(rng))
    width = high - low
    pad = tol.partial_rel * (width if width > 0 else (abs(high) or 1.0))
    statuses, notes = [], []
    for r in rep_list:
        if low <= r <= high:
            statuses.append("match"); notes.append(f"{r:g} ∈ [{low:g},{high:g}] → match")
        elif low - pad <= r <= high + pad:
            statuses.append("partial"); notes.append(f"{r:g} within padded [{low-pad:g},{high+pad:g}] → partial")
        else:
            statuses.append("no_match"); notes.append(f"{r:g} ∉ [{low:g},{high:g}] → no_match")
    return _aggregate(statuses), "; ".join(notes)


def _flatten_table_pairs(rep_table: Any, paper_table: Any) -> Optional[List[Tuple[float, float]]]:
    """Best-effort align two flat-dict tables ({key: number}) into (rep, paper)
    numeric pairs by exact key. Returns None if not both flat dicts, or if any
    expected paper key is absent / non-numeric in the replicated table.

    Key fidelity is exact (PR #66): a mutated/missing key yields no pair for that
    cell, which the caller treats as a no_match cell — exactly the CB Mode-2
    failure this split is meant to catch deterministically.
    """
    if not (isinstance(rep_table, dict) and isinstance(paper_table, dict)):
        return None
    pairs: List[Tuple[float, float]] = []
    for k, pv in paper_table.items():
        if not _is_number(pv):
            return None
        rv = rep_table.get(k)
        if not _is_number(rv):
            # missing or mutated key, or non-numeric -> count as a failed cell
            pairs.append((float("nan"), float(pv)))
        else:
            pairs.append((float(rv), float(pv)))
    return pairs or None


def _grade_table(structured: dict, tol: GradingTolerances) -> GradeResult:
    if not structured.get("value_found", True):
        return "not_attempted", "comparator reported no replicated table was produced"
    rep_table = structured.get("replicated_table")
    paper_table = structured.get("paper_table")
    pairs = _flatten_table_pairs(rep_table, paper_table)
    if pairs is None:
        # Not a clean flat-dict table we can grade deterministically; defer to
        # the comparator's proposed status (passthrough) rather than guess.
        return "__passthrough__", "table shape not deterministically gradable; kept comparator judgment"
    statuses, n_bad = [], 0
    for r, p in pairs:
        if r != r:  # NaN -> missing/mutated cell
            statuses.append("no_match"); n_bad += 1
            continue
        s, _ = _grade_one_scalar(r, p, None, tol)
        statuses.append(s)
        if s == "no_match":
            n_bad += 1
    agg = _aggregate(statuses)
    return agg, f"{len(pairs)} cell(s), {n_bad} outside tolerance → {agg}"


def grade_claim(
    claim_type: str,
    structured: dict,
    proposed_status: Optional[str],
    tol: Optional[GradingTolerances] = None,
) -> Tuple[str, str, str]:
    """Grade a claim from the comparator's output.

    Returns ``(status, rationale, graded_by)`` where ``graded_by`` is
    ``"deterministic"`` (this module decided it from the extracted value) or
    ``"llm"`` (kept the comparator's ``proposed_status`` — for non-deterministic
    claim types or a table shape we can't grade). Never invents a verdict for a
    passthrough case.
    """
    tol = tol or GradingTolerances()
    structured = structured or {}
    if claim_type not in DETERMINISTIC_TYPES:
        return (proposed_status or "not_attempted",
                "non-deterministic claim type; kept comparator judgment", "llm")
    if claim_type == "scalar":
        status, why = _grade_scalar(structured, tol)
    elif claim_type == "scalar_range":
        status, why = _grade_scalar_range(structured, tol)
    else:  # table
        status, why = _grade_table(structured, tol)
    if status == "__passthrough__":
        return (proposed_status or "not_attempted", why, "llm")
    return status, why, "deterministic"
