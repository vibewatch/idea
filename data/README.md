# Shared generated data

This directory is the contract between applications:

- `pipeline/src/idea_pipeline/scraper/` is the only writer.
- `pipeline/src/idea_pipeline/analyzer/` reads snapshots but never mutates them.
- The root Astro website consumes snapshots as read-only build input.

Reddit snapshots live at `reddit/<topic>/<YYYY-MM-DD>.json`. Optional Show HN
and Ask HN snapshots live at `hackernews/<stream>/<YYYY-MM-DD>.json`. They are
committed so analysis and static builds can run without contacting source APIs
and historical output remains reproducible.

Do not store credentials, cookie files, temporary scraper output, or website build artifacts here.
Derived Markdown belongs under `reports/`, while temporary analysis dossiers and logs belong under ignored `pipeline/artifacts/`.
