# Idea

A root-level Astro website backed by a separate Python collection and analysis pipeline.

## Repository layout

```text
.
├── pipeline/             # One Python project and dependency environment
│   ├── config/
│   │   ├── scraper/      # Collection configuration
│   │   └── refresher/    # Credential-refresh configuration
│   ├── src/idea_pipeline/
│   │   ├── analyzer/     # Ranked preparation and evidence-grounded reports
│   │   ├── scraper/      # Reddit data collection
│   │   └── refresher/    # Browser-cookie renewal
│   └── tests/            # Mirrored analyzer/scraper/refresher tests
├── data/                 # Versioned JSON consumed by Astro at build time
│   └── reddit/           # Daily snapshots grouped by topic
├── reports/              # Versioned intelligence reports derived from snapshots
│   └── reddit/           # One combined daily report across all three topics
├── .github/workflows/    # Pipeline and future website automation
└── <Astro files>         # package.json, astro.config.*, src/, public/, etc.
```

The Astro project belongs directly in the repository root. Collection, analysis, and cookie refresh are sibling subsystems in the independently installable `pipeline/` project. `data/` is immutable analysis input; `reports/` contains validated derived content. Astro may consume both as read-only build inputs.

## Pipeline quick start

```bash
uv sync --project pipeline
uv tool install rdt-cli
uv run --project pipeline scrape-reddit
uv run --project pipeline analyze-reddit --prepare-only
```

Add `REDDIT_COOKIES` to `pipeline/.env` before a live collection. Keeping pipeline secrets below `pipeline/` prevents Astro from loading them as root website environment variables.

See [`pipeline/README.md`](pipeline/README.md) for scraping, refresh, configuration, and test commands.

The daily analysis workflow is defined in `.github/workflows/analyze_reddit.yml`. It analyzes completed snapshots into source-linked project, pain-point, idea/validation, launch-result, and inspected-media tables; validates staged Markdown and its media-review ledger; then commits `reports/reddit/`. Configure the `COPILOT_PAT` Actions secret before enabling it.

The scheduled cookie refresh pipeline is defined in `.github/workflows/refresh_reddit_cookies.yml`. It renews the browser session and replaces the `REDDIT_COOKIES` Actions secret without exposing it to the website.

## Astro website

Initialize Astro directly in this root directory when ready. Website code should treat `data/` and `reports/` as read-only build inputs. Only `idea_pipeline.scraper` writes snapshots, and only `idea_pipeline.analyzer` publishes reports.
