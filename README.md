# Idea

A static Astro website backed by a separate Reddit scraping pipeline.

## Repository layout

```text
.
├── scraper/              # Independent Python collection project
│   ├── config/           # Reddit topic configuration
│   ├── src/              # idea_scraper package
│   └── tests/            # Scraper unit tests
├── data/                 # Versioned JSON consumed by Astro at build time
│   └── reddit/           # Daily snapshots grouped by topic
├── .github/workflows/    # Scraper and future website automation
└── <Astro files>         # package.json, astro.config.*, src/, public/, etc.
```

The Astro project belongs directly in the repository root. The scraper remains independently installable under `scraper/`, and `data/` is the read-only boundary between them.

## Scraper quick start

```bash
uv sync --project scraper
uv tool install rdt-cli
uv run --project scraper scrape-reddit
```

Add `REDDIT_COOKIES` to `scraper/.env` before a live collection. Keeping scraper secrets below `scraper/` prevents Astro from loading them as root website environment variables.

See [`scraper/README.md`](scraper/README.md) for configuration, commands, output format, and tests.

## Astro website

Initialize Astro directly in this root directory when ready. Website code should treat `data/` as read-only build input; only the scraper writes snapshots.
