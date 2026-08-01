# Veritas

Veritas evaluates whether scientific papers can be reproduced: it extracts a paper's verifiable claims, replicates the methodology, and adjudicates each claim against the produced evidence into a tier-weighted Replication Score.

## Language

**Claim**:
One verifiable fact a paper asserts about its results, extracted into `paper_claims.json` and later adjudicated against replication evidence. Shape-typed (`scalar | scalar_range | table | qualitative | figure`).

**Headline claim**:
A claim stating the paper's central reproducible result. Usually 1-3 per paper, drawn from the abstract or marquee figure — a calibration prior, not a cap: a paper with four co-equal central results has four headline claims. One of two tiers; the other is **supporting**. A stated "contribution" is a headline claim only if it is a reproducible result (a released dataset or proposed framework is not).

**Supporting claim**:
A claim about an intermediate measurement, secondary figure, or qualitative observation that builds toward a headline claim.

**Claim scope**:
Which claims a run extracts and therefore replicates and verifies. `main` = headline claims only (the default); `full` = headline + supporting; a positive integer N = exactly the N most central claims ("N and only N").
_Avoid_: "mode" (taken by input mode: full / paper-only / repo-only), "extraction mode" (a deleted vestigial field).

**Main scope**:
Claim scope that selects only headline-tier claims — the 1-3 central results. Not a fixed count: a paper with co-equal central results keeps all of them. When an exact count is wanted, a numeric scope (e.g. `--scope 1`) demands it explicitly; `main` never does.

**Numeric scope**:
Claim scope given as a positive integer N: extract exactly the N most central reproducible claims, ranked by centrality. A hard count, unlike main scope's soft 1-3.

**Full scope**:
Claim scope that selects every extractable claim (headline + supporting). The only behavior that existed before claim scope was introduced.

## Flagged ambiguities

- **"full"** is overloaded: input mode `full` means "paper + repo provided"; claim scope `full` means "extract all claim tiers". The two are independent axes and both spellings are kept for CLI ergonomics; disambiguate in prose as "full input mode" vs "full claim scope".
- A `--scope` flag existed until June 2026 with different semantics (whether to extract the deleted `setup` tier). Any reference to "scope" in pre-June documents means that old axis, not claim scope.

## Example dialogue

> **Dev:** The econ pipeline wants a one-claim check — is that main scope?
> **Domain expert:** Close, but main scope is soft: it takes the paper's central results as the paper presents them, typically 1-3. A one-claim check is the numeric scope `1` — "extract exactly one claim, the single most central result." Either way, scope only shapes automatic extraction; a hand-authored `--claims` file bypasses it entirely.
> **Dev:** And if a run in main scope extracts three headline claims?
> **Domain expert:** Then the paper has three central results and all three are replicated at full fidelity. Main scope reduces which claims are attempted, never how thoroughly each one is replicated.
