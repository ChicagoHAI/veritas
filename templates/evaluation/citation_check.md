# Citation Check (reference/bibliographic verification)

You verify whether this paper's **references actually exist and are described
correctly** (title, authors, year, venue, identifiers). You do NOT judge whether
a cited paper supports the claim it is cited for — that is out of scope here.

A deterministic verification script does the authoritative existence/metadata
check against scholarly databases. Your job is to (1) extract the reference list
for it, (2) run it, and (3) investigate only the references it could not resolve.

## Source of truth

- **Paper:** `{{ paper_path }}` — read its bibliography / "References" section.

## Step 1 — Extract the reference list

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

Transcribe faithfully. Do not invent or correct anything at this step — if the
paper cites a work as an arXiv preprint, write `"arXiv preprint ..."` in `venue`
even if you believe it was later published.

If the paper has no reference section, or you cannot read it (for example a
scanned, image-only PDF), write an empty list `[]` to `{{ references_path }}`
and continue. The resolver handles an empty list and the result will report
`total: 0`. Do not invent references.

## Step 2 — Run the deterministic resolver (authoritative)

Run the verification script over your extracted references:

```bash
python {{ resolver_script_path }} {{ references_path }} {{ resolver_verdicts_path }}
```

It queries Crossref, OpenAlex, Semantic Scholar, DBLP, and arXiv and writes a
verdict per reference: `verified`, `metadata_mismatch`, or `unresolved`, with the
authoritative record it found. **These verdicts are authoritative. Do not
override `verified` or `metadata_mismatch`.** They were produced by matching
real database records, which is more reliable than a web search. Read
`{{ resolver_verdicts_path }}`.

Run the script once over the whole list (it processes every reference). If the
script fails (non-zero exit), writes no file, or writes invalid JSON, do NOT
invent verdicts: treat every reference as `unresolved` and proceed to Step 3.

## Step 3 — Escalate ONLY the `unresolved` references

For each reference the script marked `unresolved`, do a careful web search to
decide whether it is real but just missing from those databases, or genuinely
fabricated:

- Search for the exact title in quotes, plus the authors. A real work has its
  own dedicated page (publisher, arXiv abstract, DBLP, the authors' site) — not
  merely an appearance inside someone else's reference list.
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

## Step 4 — Write the result

Write `{{ citation_check_path }}` as a single JSON object:

```json
{
  "summary": {
    "total": 0, "verified": 0, "metadata_mismatch": 0,
    "unresolved": 0, "likely_fabricated": 0, "inconclusive": 0
  },
  "flagged": [
    {
      "key": "<ref key>",
      "raw": "<verbatim reference line>",
      "status": "metadata_mismatch | likely_fabricated | inconclusive",
      "detail": "<one plain sentence: what is wrong, e.g. 'cited as arXiv preprint but published at ICLR 2024 per DBLP'>",
      "matched_record": "<the resolver's matched_record object for this reference, copied verbatim from the resolver output; null for references you escalated by web search>",
      "evidence": ["<source URL(s) you used for an escalated reference; use [] for resolver-set entries>"]
    }
  ],
  "checked_support": false,
  "notes": "Citation support (whether each source backs the attributed claim) was not checked in this version."
}
```

Rules:
- `summary.total` is the number of references. The five status counts must sum
  to `total`. `unresolved` in the summary should be 0 after escalation (each
  unresolved reference becomes `likely_fabricated` or `inconclusive`); if you
  could not escalate one, leave it `unresolved` and count it.
- List in `flagged` every reference whose final status is NOT `verified`.
  `verified` references are counted only, not listed.
- Copy `matched_record` verbatim from the resolver output for that reference's
  key. For references you escalated by web search, set it to `null`.
- `evidence` is `[]` for resolver-set entries (`metadata_mismatch`); for escalated
  entries it lists the URL(s) you used.
- Keep `checked_support` exactly `false`. Print the JSON to stdout as well.

Begin now.
