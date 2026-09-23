# Derived analysis reports

This directory contains versioned Builder Intelligence Reports generated from the immutable snapshots under `data/`.

Builder Intelligence reports use this contract:

```text
reports/builder/<YYYY-MM-DD>.md
```

The Python analyzer combines the same-date required Reddit streams with any
available Hacker News enrichment, stages model output under ignored
`pipeline/artifacts/`, validates structure and citation boundaries, and
atomically publishes only valid files here. It never changes source JSON. Each
report uses one evidence ledger for projects, validation, outcomes, failures,
and visual proof, followed by compact problem, synthesis, and watchlist
sections.

Run `uv run --project pipeline analyze-builder --prepare-only` to inspect deterministic ranked artifacts plus full-corpus external-link and media manifests without downloading media or calling Copilot. See `pipeline/README.md` for generation options and required credentials.
