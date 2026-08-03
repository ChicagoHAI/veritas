"""Parsing for paper-claim extraction LLM responses."""

import json
from typing import List, Tuple

from veritas.core.models.paper_claims import PaperClaim, PaperClaims, Provenance
from veritas.core.replication import _extract_json


def parse_paper_claims_response(response: str) -> PaperClaims:
    """Parse an LLM response into a ``PaperClaims`` object.

    Expected JSON shape::

        {
            "paper": {"title": "...", "arxiv_id": "...", ...},
            "claims": [
                {
                    "id": "C1",
                    "description": "...",
                    "type": "scalar",
                    "tier": "headline",
                    "paper_value": 92.3,
                    "units": "%",
                    "expected_output_file": null,
                    "provenance": {"section": "Abstract", "page": 1, "quote": "..."},
                    "verification": "...",
                    "notes": null
                }
            ]
        }
    """
    try:
        raw = _extract_json(response)
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Could not parse paper claims response as JSON: {e}")

    return PaperClaims.from_dict(data)


def enforce_claim_scope(
    claims: "PaperClaims", claim_scope: str
) -> "Tuple[PaperClaims, List[str], List[str]]":
    """Deterministically enforce the extraction-scope contract on parsed claims.

    The extraction prompt already instructs the agent per scope; this guard
    makes the scope a hard guarantee. Returns ``(kept, dropped_ids,
    warnings)``. Never turns a non-empty claim set into an empty one: when
    filtering would drop everything, the set is kept unfiltered and a
    warning explains why.
    """
    if claim_scope == "full":
        return claims, [], []

    if claim_scope.isdigit():
        n = int(claim_scope)
        if n < 1:
            return claims, [], [
                f"scope {n} is not a positive count; keeping all claims"
            ]
        if len(claims.claims) <= n:
            warnings = []
            if len(claims.claims) < n:
                warnings.append(
                    f"scope {n} requested but only {len(claims.claims)} "
                    f"claim(s) extracted; keeping all"
                )
            return claims, [], warnings
        # The guard enforces exactly N; it keeps the first N in extraction
        # order. Centrality ranking is trusted from the extractor (the prompt
        # asks for most-central-first) and is not recomputed here.
        kept = PaperClaims(
            paper=claims.paper, claims=claims.claims[:n], scope=claims.scope
        )
        return kept, [c.id for c in claims.claims[n:]], []

    # main scope: headline tier only.
    headline = claims.by_tier("headline")
    if not headline:
        return claims, [], [
            "main scope: extractor returned no headline-tier claims; "
            "keeping all claims unfiltered"
        ]
    dropped = [c.id for c in claims.claims if c.tier != "headline"]
    if not dropped:
        return claims, [], []
    kept = PaperClaims(paper=claims.paper, claims=headline, scope=claims.scope)
    return kept, dropped, []
