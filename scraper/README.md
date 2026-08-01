# Reddit scraper

The Python application that collects product ideas and customer pain points from Reddit communities such as `r/SaaSBuild`, `r/SaaS`, `r/Startup_Ideas`, and `r/smallbusiness`.

The scraper is intentionally isolated from the root Astro application. It owns collection code, configuration, and secrets under `scraper/`, and writes the shared dataset under `data/reddit/`.

## Pipeline

1. Load validated topic groups from `scraper/config/reddit.yml`.
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
- A Reddit session cookie exported from a browser account that may access the configured public communities

Use collection responsibly: review Reddit's current terms, respect community rules, avoid private/restricted content, and keep request volume conservative.

## Setup

From the repository root, install the Python project and Reddit CLI:

```bash
uv sync --project scraper
uv tool install rdt-cli
```

Put browser-exported Playwright-style cookie JSON in `scraper/.env`:

```dotenv
REDDIT_COOKIES='[{"name":"reddit_session","value":"replace-with-your-value"}]'
```

`scraper/.env` is ignored by Git and kept separate from Astro's root environment. The scraper converts this array to the credential format expected by `rdt-cli` and writes it to `~/.config/rdt-cli/credential.json` with mode `0600`.

## Run

From the repository root, run the scraper with the following commands. Its config, data, and environment defaults are anchored to the repository layout rather than the current working directory:

```bash
uv run --project scraper scrape-reddit
uv run --project scraper scrape-reddit --name saas-build
uv run --project scraper scrape-reddit --data-dir /tmp/reddit-ideas
uv run --project scraper scrape-reddit --comments 0
```

## Configuration

Edit `scraper/config/reddit.yml` to add or remove communities:

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

## Development

All external CLI calls are mocked in tests:

```bash
uv run --project scraper pytest scraper/tests -v
uv run --project scraper ruff check scraper
```
