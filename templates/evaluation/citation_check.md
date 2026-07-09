# Citation Check (reference/bibliographic verification)

You verify whether this paper's **references actually exist and are described
correctly** (title, authors, year, venue, identifiers). You also judge, for the
paper's main claims, whether the cited source supports what the paper attributes
to it (Step 4).

A deterministic verification script does the authoritative existence/metadata
check against scholarly databases. Your job is to (1) extract the reference list
for it, (2) run it, and (3) investigate only the references it could not resolve.

## Source of truth

- **Paper:** `{{ paper_path }}`. Read its bibliography / "References" section.

## Step 1: Extract the reference list

Read the paper and write every reference to `{{ references_path }}` as a JSON
list. For each reference include what you can read; leave a field empty if the
paper does not state it. Use this shape exactly:

```json
[
  {
    "key": "<short label, e.g. first-author+year>",
    "title": "<paper title>",
    "authors": ["<author 1>", "<author 2>"],
    "year": 2024,
    "venue": "<venue/journal as printed, e.g. 'ICLR' or 'arXiv preprint arXiv:2401.01234'>",
    "doi": "<doi if printed, else ''>",
    "arxiv_id": "<arxiv id if printed, else ''>",
    "raw": "<the full reference line, verbatim>"
  }
]
```

Transcribe faithfully. Do not invent or correct anything at this step. If the
paper cites a work as an arXiv preprint, write `"arXiv preprint ..."` in `venue`
even if you believe it was later published.

If the paper has no reference section, or you cannot read it (for example a
scanned, image-only PDF), write an empty list `[]` to `{{ references_path }}`
and continue. The resolver handles an empty list and the result will report
`total: 0`. Do not invent references.

## Step 2: Run the deterministic resolver (authoritative)

Run the verification script over your extracted references:

```bash
python "{{ resolver_script_path }}" "{{ references_path }}" "{{ resolver_verdicts_path }}"
```

It queries Crossref, OpenAlex, Semantic Scholar, DBLP, and arXiv and writes a
verdict per reference: `verified`, `metadata_mismatch`, or `unresolved`, with the
authoritative record it found. **`verified` and `metadata_mismatch` are
authoritative: never downgrade one, and the only permitted upgrade is the
empty-venue check below (a `verified` may become `metadata_mismatch`).
`unresolved` verdicts are yours to settle in Step 3.** They were produced by
matching real database records, which is more reliable than a web search.
Read `{{ resolver_verdicts_path }}`.

Run the script once over the whole list (it processes every reference). If the
script fails (non-zero exit), writes no file, or writes invalid JSON, do NOT
invent verdicts: treat every reference as `unresolved` and proceed to Step 3.

For any reference the resolver marked `verified` but for which its `matched_record`
has an empty `venue`, the resolver could not assess publication status. Do a quick
web search for the work's canonical record (publisher page, DBLP, the venue site).
If you find it was published at a real venue but the paper cites it as an arXiv
preprint (or with no venue), record it as `metadata_mismatch` with a one-line
`detail` ("published at <venue> <year> per <source>") and the source URL in
`evidence`, quoting the venue line you saw. If you cannot confirm a published
venue, leave the resolver's `verified` as-is. Never downgrade a confident
`metadata_mismatch` the resolver already produced.
This is a low-severity, informational metadata note (status `metadata_mismatch`),
not a fabrication; only record it when you have confirmed the published venue from a
real source.

## Step 3: Escalate ONLY the `unresolved` references

For each reference the script marked `unresolved`, do a careful web search to
decide whether it is real but just missing from those databases, or genuinely
fabricated:

- Search for the exact title in quotes, plus the authors. A real work has its own dedicated page (publisher, arXiv abstract, DBLP, the authors' site). It does not appear only inside someone else's reference list.
- If you find the work, classify it `inconclusive` and record the source URL
  (the resolver simply missed it; do not call it verified).
- Only if you find clear evidence it does not exist (no dedicated page anywhere;
  title/author/venue combination returns nothing real) classify it
  `likely_fabricated`, with the searches you ran in `evidence`.
- **When in doubt, choose `inconclusive`.** A false accusation of fabrication is
  worse than a miss. Never auto-correct a reference; only flag and cite evidence.

Aim to turn every `unresolved` reference into `inconclusive` or
`likely_fabricated`. Leave a reference `unresolved` (and count it as such) only
if you genuinely could not complete a web search for it.

## Step 4: Check faithfulness for the main claims

Faithfulness asks: does the cited source actually support what THIS paper attributes
to it? Check this for the paper's **main claim-bearing citations**: the citations
its core argument depends on (its central motivation, the provenance of its method,
its key baselines/comparisons). Skip generic "see also" / background cites.

{% if faithfulness_scope == "all" %}
**Scope: ALL.** Check every claim-bearing citation. That is every citation where the paper
attributes a specific factual claim to the source, not only the central ones.
When in doubt about whether a citation is claim-bearing, include it.
{% else %}
**Scope: MAIN.** Check only the citations central to the paper's argument. When in
doubt about whether a citation is central, skip it.
{% endif %}

For each selected (claim, citation) pair:
1. Identify the exact claim the paper attributes to the source (quote the citing
   sentence).
2. Retrieve the cited source's text (open-access page, arXiv abstract/HTML, publisher
   page). Read the relevant part.
3. Decide the **content verdict** by comparing the source to the attributed claim:
   - `supported`: the source substantively states the attributed claim.
   - `partially_supported`: directionally correct but over-claimed. Narrower scope,
     weaker evidence type (e.g. the source shows correlation, the paper says it
     "causes"), or dropped caveats.
   - `contradicted`: the source states the opposite, reverses, or materially
     misrepresents the claim.
   - `not_mentioned`: the source is on-topic but silent on the specific claim. If the source appears to be the wrong work entirely, say so in `detail` and use `not_mentioned`.
4. If you cannot retrieve the source, set `source_status` to `inaccessible` and give
   no content verdict. Do NOT guess. Otherwise `source_status` is `retrieved`.

**Every `supported`, `partially_supported`, or `contradicted` verdict MUST include a
verbatim `quote` from the cited source (<= 200 characters) that justifies it.** No
quote, no such verdict. Tag how you obtained the source in `evidence_basis`
(`provided | fetched_full | fetched_snippet | inaccessible`). When genuinely unsure
between two content verdicts, prefer the less severe one and explain in `detail`.

## Step 5: Write the result

Write `{{ citation_check_path }}` as a single JSON object:

```json
{
  "summary": {
    "total": 0, "verified": 0, "metadata_mismatch": 0,
    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0,
    "faithfulness": {
      "checked": 0, "supported": 0, "partially_supported": 0,
      "contradicted": 0, "not_mentioned": 0, "inaccessible": 0
    },
    "faithfulness_scope": "{{ faithfulness_scope }}"
  },
  "flagged": [
    {
      "key": "<ref key>",
      "raw": "<verbatim reference line>",
      "status": "metadata_mismatch | likely_fabricated | inconclusive | unresolved",
      "detail": "<one plain sentence on what is wrong>",
      "matched_record": "<the resolver's matched_record object, or null for web-escalated refs>",
      "evidence_basis": "provided | fetched_full | fetched_snippet | inaccessible",
      "evidence": ["<source URL(s); [] for resolver-set entries>"]
    }
  ],
  "faithfulness": [
    {
      "key": "<ref key of the cited source>",
      "claim": "<the claim the paper attributes to the source, quoted from the citing sentence>",
      "source_status": "retrieved | inaccessible",
      "verdict": "supported | partially_supported | contradicted | not_mentioned (use null when source_status is inaccessible)",
      "quote": "<verbatim quote from the cited source (<= 200 chars); '' only if source_status is inaccessible>",
      "evidence_basis": "provided | fetched_full | fetched_snippet | inaccessible",
      "source": "<URL of the cited source you read>",
      "detail": "<one plain sentence on the judgment>"
    }
  ],
  "checked_support": true,
  "notes": "<optional caveats>"
}
```

Rules:
- The five integrity counts (`verified`, `metadata_mismatch`, `unresolved`,
  `likely_fabricated`, `inconclusive`) must sum to `total`. List in `flagged`
  every reference whose integrity status is NOT `verified`, including any left
  `unresolved`.
- `faithfulness` lists every (claim, citation) pair you checked, including
  `supported` ones. Before writing, self-check the faithfulness counts: `summary.faithfulness.checked`
  must equal the number of entries in `faithfulness[]`, and
  `supported + partially_supported + contradicted + not_mentioned + inaccessible`
  must equal `checked`. `not_mentioned` is a verdict for a retrieved source (the
  source was read but is silent on the claim); `inaccessible` counts entries whose
  `source_status` is `inaccessible`.
- When `source_status` is `inaccessible`, set `verdict` to `null` and `quote` to `""`.
- Set `summary.faithfulness_scope` to `{{ faithfulness_scope }}`.
- Set `checked_support` to `false` only if you could not perform Step 4 at all;
  otherwise keep it `true`. A `supported`/`partially_supported`/
  `contradicted` entry without a `quote` is invalid. Fix it or downgrade to
  `not_mentioned`/`inaccessible`.
- Copy `matched_record` verbatim from the resolver output for that reference's
  key. Set it to `null` only for Step 3 escalations of `unresolved` references;
  an entry from the Step 2 empty-venue check keeps its resolver record.
- `evidence` is `[]` for entries taken unchanged from the resolver; for
  web-checked entries (a Step 3 escalation or the Step 2 empty-venue check) it
  lists the URL(s) you used.
- For `flagged` entries, `evidence_basis` is `provided` when the resolver matched a
  database record; for a web-escalated entry use the basis of the page you read
  (`fetched_full` or `fetched_snippet`), or `inaccessible` if you could not open it.

Begin now.
