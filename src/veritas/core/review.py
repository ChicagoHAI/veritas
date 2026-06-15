"""Read-mode static-review parsing and aggregation.

The static-review agent writes one combined JSON object: the aggregate
Reproducibility Assessment fields at the top level plus a ``claims`` array of
per-claim assessments. This module parses that, and recomputes the
``support_breakdown`` / ``total_claims`` deterministically from the claim list
so the headline counts are code-computed (not the agent's self-report).
"""

from __future__ import annotations

import json
from typing import List, Tuple

from veritas.core.models.review import (
    ClaimAssessment,
    ReproducibilityAssessment,
    summarize_support,
)
from veritas.core.replication import _extract_json


def parse_review_response(
    text: str,
) -> Tuple[ReproducibilityAssessment, List[ClaimAssessment]]:
    """Parse the combined static-review JSON into (aggregate, per-claim list).

    Raises ``ValueError`` if the payload can't be located or is not a JSON
    object. The aggregate's ``support_breakdown`` and ``total_claims`` are
    recomputed from the parsed claim assessments.
    """
    raw = _extract_json(text)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("static-review output is not a JSON object")

    claims_data = data.get("claims", [])
    if not isinstance(claims_data, list):
        raise ValueError("static-review 'claims' field is not a list")
    assessments = [ClaimAssessment.from_dict(c) for c in claims_data]

    aggregate = ReproducibilityAssessment.from_dict(data)
    # Code-computed, not agent-reported.
    aggregate.support_breakdown = summarize_support(assessments)
    aggregate.total_claims = len(assessments)
    return aggregate, assessments
