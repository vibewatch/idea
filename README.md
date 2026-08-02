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
├── src/                  # Astro pages, layouts, components, and report utilities
├── public/               # Static brand, crawler, and web-app assets
├── .github/workflows/    # Pipeline and GitHub Pages automation
└── astro.config.mjs      # Static site configuration for /idea/
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

The root Astro project loads `reports/reddit/*.md` directly at build time. Reports remain pipeline-owned, read-only artifacts; the website derives titles, dates, summaries, counts, archive cards, RSS entries, and static report routes without requiring frontmatter or duplicating content under `src/`.

Requirements: Node.js 22.12 or newer and npm.

```bash
npm install
npm run dev
```

The development URL includes the configured repository base: `http://localhost:4321/idea/`.

Production validation and build:

```bash
npm run check
npm run build
npm run preview
```

`npm run build` prerenders the full site and then creates a browser-side Pagefind index in `dist/pagefind/`. The site itself has no server runtime, analytics, or private environment variables.

### Website routes

- `/idea/` — latest dispatch and extracted signal board
- `/idea/reports/` — chronological archive with metadata filtering
- `/idea/reports/<YYYY-MM-DD>/` — source-linked Markdown report with a table of contents
- `/idea/about/` — collection, extraction, and validation methodology
- `/idea/rss.xml` — report feed

### GitHub Pages

`.github/workflows/deploy_site.yml` uses the official Astro Pages action and deploys to `https://vibewatch.github.io/idea/`. It runs for website/report changes and after a successful `Extract Reddit value report` workflow, including report commits made by GitHub Actions. Set **Settings → Pages → Build and deployment → Source** to **GitHub Actions** once for the repository.

For a local root-path build, override the public URL without editing configuration:

```bash
ASTRO_SITE=http://localhost:4321 ASTRO_BASE=/ npm run build
```

Website code treats `data/`, `reports/`, and `pipeline/` as read-only inputs. Only `idea_pipeline.scraper` writes snapshots, and only `idea_pipeline.analyzer` publishes reports.
