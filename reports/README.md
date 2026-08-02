# Derived analysis reports

This directory contains versioned Builder Intelligence Reports generated from the immutable snapshots under `data/`.

Reddit reports use this contract:

```text
reports/reddit/<YYYY-MM-DD>.md
```

The Python analyzer combines the same-date `customer-pain`, `startup-ideas`, and `saas-build` snapshots, stages model output under ignored `pipeline/artifacts/`, validates structure and citation boundaries, and atomically publishes only valid files here. It never changes source JSON. Each report exposes reusable tables for directly linked projects, concrete pain points/workarounds, founder ideas and validation, launch/traction results, and inspected visual/demo evidence before presenting bounded cross-stream matches and a watchlist. These stable tables are suitable for the future root-level Astro build.

Run `uv run --project pipeline analyze-reddit --prepare-only` to inspect deterministic ranked artifacts plus full-corpus external-link and media manifests without downloading media or calling Copilot. See `pipeline/README.md` for generation options and required credentials.
