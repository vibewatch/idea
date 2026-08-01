# Idea

A root-level Astro website backed by a separate Python data pipeline.

## Repository layout

```text
.
├── pipeline/             # One Python project and dependency environment
│   ├── config/
│   │   ├── scraper/      # Collection configuration
│   │   └── refresher/    # Credential-refresh configuration
│   ├── src/idea_pipeline/
│   │   ├── scraper/      # Reddit data collection
│   │   └── refresher/    # Browser-cookie renewal
│   └── tests/            # Mirrored scraper/refresher tests
├── data/                 # Versioned JSON consumed by Astro at build time
│   └── reddit/           # Daily snapshots grouped by topic
├── .github/workflows/    # Pipeline and future website automation
└── <Astro files>         # package.json, astro.config.*, src/, public/, etc.
```

The Astro project belongs directly in the repository root. Scraping and cookie refresh are sibling subsystems in the independently installable `pipeline/` project. `data/` is the read-only boundary between Python and Astro.

## Pipeline quick start

```bash
uv sync --project pipeline
uv tool install rdt-cli
uv run --project pipeline scrape-reddit
```

Add `REDDIT_COOKIES` to `pipeline/.env` before a live collection. Keeping pipeline secrets below `pipeline/` prevents Astro from loading them as root website environment variables.

See [`pipeline/README.md`](pipeline/README.md) for scraping, refresh, configuration, and test commands.

The scheduled cookie refresh pipeline is defined in `.github/workflows/refresh_reddit_cookies.yml`. It renews the browser session and replaces the `REDDIT_COOKIES` Actions secret without exposing it to the website.

## Astro website

Initialize Astro directly in this root directory when ready. Website code should treat `data/` as read-only build input; only `idea_pipeline.scraper` writes snapshots.
