# Vendored from OpenAIReview

This package is a vendored copy of OpenAIReview's `reviewer` package
(https://github.com/ChicagoHAI/OpenAIReview, `src/reviewer/`), copied into
veritas so the paper-reviewing engine and the in-line-comment viewer assets are
available in-repo and Docker-reproducible. Both projects are from the same lab.

- Imports are relative, so this is a faithful copy — re-sync by replacing this
  directory with upstream `src/reviewer/`.
- LLM calls go through `client.py`, which reads API keys from the environment
  (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY / GEMINI_API_KEY /
  MISTRAL_API_KEY). veritas loads these from the shared `.env`.
- `viz/index.html` is the in-line-comment viewer reused by veritas.
