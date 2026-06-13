# Citation Audit (independent re-check of flagged verdicts)

A first pass already checked this paper's citations and wrote its findings to
`{{ citation_check_path }}`. You are an **independent auditor**. Re-examine ONLY the
**flagged** findings, from scratch, without trusting the first pass's reasoning. Your
job is to catch mistakes the first pass made — both false alarms and missed problems.

## Source of truth

- **Paper:** `{{ paper_path }}` — for the citing sentences and reference list.
- **First-pass findings:** `{{ citation_check_path }}` — read it.

## What to re-check

Re-check every entry in the first pass's `flagged` array (integrity issues:
`metadata_mismatch`, `likely_fabricated`, `inconclusive`) and every `faithfulness`
entry whose `verdict` is `contradicted` or `partially_supported`. Ignore
`verified` references and `supported` / `not_mentioned` faithfulness entries — those
are not worth the re-check.

For each re-checked item:
1. Independently retrieve the relevant record or cited source yourself (do not rely
   on the first pass's quote or links — find it again).
2. Form your own verdict using the same vocabulary (integrity:
   `verified | metadata_mismatch | likely_fabricated | inconclusive`; faithfulness:
   `supported | partially_supported | contradicted | not_mentioned`, or
   `inaccessible` if you cannot retrieve the source).
3. Compare to the first pass's verdict.

When your verdict differs from the first pass's, that item goes to **human review** —
do NOT try to decide who is right. When you genuinely cannot retrieve a source to
re-check, do not record a disagreement (an inability to re-check is not a dispute).

## Output

Write `{{ citation_audit_path }}` as a single JSON object:

```json
{
  "audited_count": 0,
  "human_review": [
    {
      "key": "<ref key>",
      "kind": "integrity | faithfulness",
      "first_verdict": "<the first pass's status/verdict>",
      "audit_verdict": "<your independent verdict>",
      "note": "<one plain sentence on the disagreement, with a verbatim quote if relevant>"
    }
  ]
}
```

- `audited_count` is how many flagged items you re-checked.
- `human_review` lists only the items where your verdict differs from the first
  pass's. If you agree with everything (or could not re-check), `human_review` is `[]`.
- Print the JSON to stdout as well.

Begin now.
