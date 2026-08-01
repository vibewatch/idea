# Shared generated data

This directory is the contract between applications:

- `scraper/` is the only writer.
- The root Astro website consumes snapshots as read-only build input.

Reddit snapshots live at `reddit/<topic>/<YYYY-MM-DD>.json`. They are committed so a static Astro build can run without contacting Reddit and historical pages remain reproducible.

Do not store credentials, cookie files, temporary scraper output, or website build artifacts here.
