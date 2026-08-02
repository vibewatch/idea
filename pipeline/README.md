# Python pipeline

One Python project hosts three sibling subsystems: Reddit data collection, report analysis, and browser-cookie refresh. They share a lockfile, environment, path utilities, and logging without importing each other's implementation.

```text
pipeline/
├── config/
│   ├── scraper/reddit.yml
│   └── refresher/reddit.yml
├── src/idea_pipeline/
│   ├── analyzer/reddit.py
│   ├── scraper/reddit.py
│   └── refresher/{browser,config,extract,github}.py
└── tests/
  ├── analyzer/test_reddit.py
  ├── scraper/test_reddit.py
  └── refresher/test_refresh.py
```

The project is isolated from the root Astro application. It owns Python code, configuration, secrets, and ignored work artifacts under `pipeline/`. Collection writes the shared dataset under `data/reddit/`; analysis publishes validated Markdown under `reports/reddit/`.

## Scraping pipeline

1. Load validated topic groups from `pipeline/config/scraper/reddit.yml`.
2. Install browser-exported cookies for `rdt-cli`.
3. Fetch posts from each configured subreddit with request jitter.
4. Deduplicate posts by Reddit ID across communities.
5. Fetch top comments for the most-discussed posts in each batch.
6. Atomically merge results into root-level daily JSON snapshots.
7. Run every six hours in GitHub Actions and commit changed snapshots.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- [`rdt-cli`](https://pypi.org/project/rdt-cli/)
- GitHub Copilot CLI for generated reports
- `ffmpeg` for six-frame Reddit video contact sheets
- Chromium installed through Playwright for cookie refreshes
- A Reddit session cookie exported from a browser account that may access the configured public communities

Use collection responsibly: review Reddit's current terms, respect community rules, avoid private/restricted content, and keep request volume conservative.

## Setup

From the repository root, install the Python project and Reddit CLI:

```bash
uv sync --project pipeline
uv tool install rdt-cli
uv run --project pipeline playwright install chromium
```

Put browser-exported Playwright-style cookie JSON in `pipeline/.env`:

```dotenv
REDDIT_COOKIES='[{"name":"reddit_session","value":"replace-with-your-value"}]'
```

`pipeline/.env` is ignored by Git and kept separate from Astro's root environment. The scraper converts this array to the credential format expected by `rdt-cli` and writes it to `~/.config/rdt-cli/credential.json` with mode `0600`.

## Run

From the repository root, run the scraper with the following commands. Its config, data, and environment defaults are anchored to the repository layout rather than the current working directory:

```bash
uv run --project pipeline scrape-reddit
uv run --project pipeline scrape-reddit --name saas-build
uv run --project pipeline scrape-reddit --data-dir /tmp/reddit-ideas
uv run --project pipeline scrape-reddit --comments 0
```

## Configuration

Edit `pipeline/config/scraper/reddit.yml` to add or remove communities:

```yaml
monitors:
  - name: saas-build
    subreddit:
      - SaaSBuild
      - SaaS
      - microsaas
    sort: top
    time: day
    max_posts: 25
    comments: 20
    comment_percentile: 75
```

| Field | Required | Description |
|---|---:|---|
| `name` | yes | Safe output-folder name; must be unique |
| `subreddit` | yes | One subreddit or a list, with or without `r/` |
| `sort` | no | `hot`, `new`, `top`, `rising`, `controversial`, or `best`; default `hot` |
| `time` | no | `hour`, `day`, `week`, `month`, `year`, or `all`; used by `top`/`controversial` |
| `max_posts` | no | Posts requested per subreddit, 1-100; default 25 |
| `comments` | no | Top comments requested per qualifying post, 0-100; default 0 |
| `comment_percentile` | no | Discussion percentile used to select posts for comments; default 75 |

A failed community does not discard successful results from others. A Reddit rate limit stops remaining requests and preserves the partial batch.

## Output

Snapshots are stored at `data/reddit/<topic>/<YYYY-MM-DD>.json`:

```json
{
  "last_fetched": "2026-08-02",
  "posts": [
    {
      "id": "abc123",
      "title": "A problem worth solving",
      "subreddit": "SaaS",
      "score": 42,
      "num_comments": 18,
      "selftext": "Post body",
      "permalink": "/r/SaaS/comments/abc123/example/",
      "comments_data": [
        {
          "id": "comment1",
          "author": "founder",
          "body": "Evidence from the discussion",
          "score": 9
        }
      ]
    }
  ]
}
```

Repeated runs update posts by ID while retaining previously collected comments when a refreshed post does not qualify for comment fetching.

## Analysis pipeline

The analyzer combines the three immutable daily JSON streams into static Markdown suitable for the future Astro site:

1. Discover exact-date sets containing `customer-pain`, `startup-ideas`, and `saas-build` snapshots.
2. Exclude today's still-changing files during automatic discovery and skip incomplete dates.
3. Skip dates that already have a full report unless `--force` is used.
4. Rank each stream independently by evidence richness using capped logarithmic engagement, detailed text/comments, quantified signals, concrete problems, and observed outcomes; thin viral posts receive a penalty.
5. Write per-stream review sets and dossiers plus full-corpus `external-links.json`, `media-manifest.json`, source hashes, and combined metadata under ignored `pipeline/artifacts/reddit/builder-intelligence/<date>/`.
6. During generation only, safely download approved Reddit/Imgur images and turn accessible Reddit DASH videos into six-frame contact sheets. Galleries, external videos, failures, and skipped items retain explicit URL/status records.
7. Attach every materialized visual to one sandboxed Copilot CLI process per date, with bounded worker concurrency, shell access disabled, built-in GitHub MCP disabled, and unrelated pipeline credentials removed.
8. Extract concrete projects, pain points, founder ideas/validation, launches/metrics, and useful visual findings into exact Markdown tables before adding bounded cross-stream synthesis.
9. Require `media-review.json` to account for every detected media item, distinguish inspected, non-substantive, and unavailable assets, and keep `report_included` consistent with the report.
10. Validate exact section/table schemas, at least eight source-derived direct project links when available, public HTTPS/source URL boundaries, inspected image/video evidence, cited Reddit post IDs, and current citations from each stream-specific section.
11. Atomically publish valid output to `reports/reddit/<date>.md`.

Raw snapshots are never rewritten. A failed generation or validation leaves any existing published report untouched.

Install and authenticate Copilot CLI locally, or set `COPILOT_GITHUB_TOKEN` in `pipeline/.env`. The GitHub workflow maps the repository secret named `COPILOT_PAT` to that environment variable.

From the repository root:

```bash
uv run --project pipeline analyze-reddit --prepare-only
uv run --project pipeline analyze-reddit
uv run --project pipeline analyze-reddit --date 2026-08-02 --prepare-only
uv run --project pipeline analyze-reddit --include-today --workers 1
uv run --project pipeline analyze-reddit --date 2026-08-02 --force
```

`--date` may be repeated, but every selected date must contain all three required topic snapshots. An explicit date may select today's snapshot, while `--include-today` only changes automatic discovery. `--prepare-only` always refreshes the combined manifests and sandbox per selected date, but never downloads media or invokes Copilot.

Model controls default to `--model grok-4.5 --effort high`. GPT-5.4, GPT-5.5, Claude Sonnet 4.6, and Claude Opus 4.6 remain available through explicit model selection; the workflow's `auto` effort uses `high` for Grok and Claude, and `xhigh` for GPT. The repository-local skill at `.agents/skills/reddit-idea-analysis/SKILL.md` defines what counts as a valuable project, pain point, idea/validation case, launch result, and visual finding. It requires directly openable links and exact tables rather than a thematic recap. The final report still maps convergence, partial support, contradictions, and missing links without opportunity scores or pretending unrelated posts form a tracked funnel. Keep downloaded media, contact sheets, model logs, and review ledgers in ignored `pipeline/artifacts/`; only validated reports are versioned.

The workflow `.github/workflows/analyze_reddit.yml` runs daily at 02:43 UTC and supports manual date, model, effort, worker, force, include-today, and prepare-only inputs.

## Cookie refresh pipeline

Reddit authentication here is browser-cookie based, so the pipeline renews the `REDDIT_COOKIES` session rather than exchanging an OAuth refresh token:

1. Read the current `REDDIT_COOKIES` Actions secret.
2. Launch Chromium with those cookies and visit Reddit.
3. Wait and scroll so Reddit can renew session cookies.
4. Capture the resulting Reddit cookie list and a screenshot.
5. Refuse to continue if Chromium returns no cookies.
6. Encrypt the new JSON with GitHub's repository public key.
7. Replace `REDDIT_COOKIES` through the GitHub Actions Secrets API.
8. Create a GitHub issue containing the status/log report; screenshots are retained as workflow artifacts for seven days.

Refresh settings live in `pipeline/config/refresher/reddit.yml`. The scheduled workflow at `.github/workflows/refresh_reddit_cookies.yml` runs at 01:23 UTC every third day and also supports manual dispatch.

### Bootstrap GitHub secrets

Configure these repository Actions secrets:

| Secret | Purpose |
|---|---|
| `REDDIT_COOKIES` | Playwright/browser-export JSON used by both collection and refresh workflows |
| `GH_PAT` | GitHub token permitted to update this repository's Actions secrets and create issues |
| `COPILOT_PAT` | Copilot CLI authentication used by the daily report-analysis workflow |

The built-in workflow `GITHUB_TOKEN` is not used for secret replacement. Keep `GH_PAT` narrowly scoped to this repository and rotate it according to your security policy.

To get the first cookie set from a local browser, install the optional extraction dependency, make sure Reddit is logged in, then extract and upload it:

```bash
uv sync --project pipeline --extra extract
uv run --project pipeline refresh-cookies extract reddit --browser chrome --output /tmp/reddit-cookies.json
gh secret set REDDIT_COOKIES < /tmp/reddit-cookies.json
rm /tmp/reddit-cookies.json
```

Supported extraction targets are Arc, Chrome, Edge, Firefox, and Brave. Browser cookie stores may require the browser to be closed and the operating system keychain to be unlocked.

### Run a refresh locally

Set `GH_TOKEN`, `GITHUB_REPOSITORY`, and `REDDIT_COOKIES` in `pipeline/.env`, then run:

```bash
uv run --project pipeline refresh-cookies
```

On Linux, a virtual display is started automatically for the headed Chromium session. To bypass it and use Chromium's headless mode:

```bash
uv run --project pipeline refresh-cookies --headless
```

## Development

All external CLI calls are mocked in tests:

```bash
uv run --project pipeline pytest pipeline/tests -v
uv run --project pipeline ruff check pipeline
```
