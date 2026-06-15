"""Parsing for paper-claim extraction LLM responses."""

import json

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
