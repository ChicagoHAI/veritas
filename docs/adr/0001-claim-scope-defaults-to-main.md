# Claim scope defaults to main (headline claims only)

Veritas originally extracted and replicated every verifiable claim a paper makes. In practice the more useful question is whether the paper's *central* results reproduce: full-claim runs spend most of their compute on supporting claims, and downstream users (including a planned econ-paper pipeline) want a main-claim check by default. We introduce a claim scope — `main` (headline tier only, typically 1-3 claims), `full` (headline + supporting), or a positive integer N (exactly the N most central claims) — applied at extraction time so the plan, replication effort, and verification all shrink with it. The default is `main`; `--scope full` / `VERITAS_CLAIM_SCOPE=full` restores the previous behavior.

## Consequences

- Replication Scores produced under different scopes are not comparable: main-scope scores answer "did the central results replicate?", full-scope scores average over the whole claim set. The producing scope is stamped into `paper_claims.json` and shown in the report.
- Benchmark results recorded before this change (ReplicationBench / CORE-Bench, May 2026) were full-scope. The benchmark drivers are deliberately left untouched; no re-runs are planned, and any future re-run must pass `--scope full` to stay comparable.
- "Main" is deliberately not a fixed count: a paper with several co-equal central results keeps all of them. An exact-count check is requested explicitly with a numeric scope (`--scope 1` is the single-claim check); a hand-authored `--claims` file bypasses scope entirely.
