# Citation Audit (independent re-check of flagged verdicts)

A first pass already checked this paper's citations and wrote its findings to
`{{ citation_check_path }}`. You are an **independent auditor**. Re-examine ONLY the
**flagged** findings, from scratch, without trusting the first pass's reasoning. Give
your own verdict for each. A separate automatic step reconciles your verdicts with
the first pass; your job is just to judge each item independently and honestly.

## Source of truth

- **Paper:** `{{ paper_path }}` for the citing sentences and reference list.
- **First-pass findings:** `{{ citation_check_path }}`. Read it.

## What to re-check

Read both the `flagged` array and the `faithfulness` array from
`{{ citation_check_path }}`. Re-check:

- every entry in `flagged` (integrity issues: `metadata_mismatch`, `likely_fabricated`,
  `inconclusive`, `unresolved`), and
- every `faithfulness` entry whose `verdict` is `contradicted` or `partially_supported`.

Do not re-check `verified` references, or `supported` / `not_mentioned` faithfulness
entries. They are not worth re-checking.

For each item:
1. Independently retrieve the relevant record or cited source yourself. Do not rely
   on the first pass's quote or links. Find it again.
2. Form your own verdict using the same vocabulary. Integrity:
   `verified | metadata_mismatch | likely_fabricated | inconclusive`. Faithfulness:
   `supported | partially_supported | contradicted | not_mentioned`, or
   `inaccessible` if you cannot retrieve the source to re-check.
3. Be honest and calibrated. If the first pass's accusation does not hold up under
   your independent re-read, say so with the milder verdict your evidence supports.

## Output

Write `{{ citation_audit_path }}` as a single JSON object:

```json
{
  "audited_count": 0,
  "items": [
    {
      "key": "<ref key, matching the first-pass entry>",
      "kind": "integrity | faithfulness",
      "claim": "<for faithfulness items: the claim text, copied verbatim from the first-pass entry; omit for integrity items>",
      "audit_verdict": "<your independent verdict from the vocabulary above>",
      "note": "<one plain sentence on your judgment, with a verbatim quote if relevant>"
    }
  ]
}
```

- `audited_count` is how many flagged items you re-checked.
- `items` has one entry per re-checked item, with your independent verdict. Do not
  reconcile or decide which pass is right. The automatic step does that.
- `claim` ties a faithfulness verdict to the specific claim it audits — two
  claims can cite the same reference, and your verdict must apply to exactly
  the one you re-checked.
- Print the JSON to stdout as well.

Begin now.
