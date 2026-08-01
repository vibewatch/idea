# Python pipeline

One Python project hosts two sibling subsystems: Reddit data collection and browser-cookie refresh. They share a lockfile, environment, path utilities, and logging without importing each other's implementation.

```text
pipeline/
├── config/
│   ├── scraper/reddit.yml
│   └── refresher/reddit.yml
├── src/idea_pipeline/
│   ├── scraper/reddit.py
│   └── refresher/{browser,config,extract,github}.py
└── tests/
  ├── scraper/test_reddit.py
  └── refresher/test_refresh.py
```

The project is isolated from the root Astro application. It owns Python code, configuration, and secrets under `pipeline/`, and writes the shared dataset under `data/reddit/`.

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
