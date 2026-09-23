# Python pipeline

One Python project hosts five sibling subsystems: Reddit collection, Hacker News collection, report analysis, Chinese translation, and browser-cookie refresh. They share a lockfile, environment, path utilities, and logging without importing each other's implementation.

```text
pipeline/
├── config/
│   ├── scraper/{hackernews,reddit}.yml
│   └── refresher/reddit.yml
├── src/idea_pipeline/
│   ├── analyzer/reddit.py
│   ├── scraper/hackernews.py
│   ├── scraper/reddit.py
│   ├── translator/zh.py
│   └── refresher/{browser,config,extract,github}.py
└── tests/
  ├── analyzer/test_reddit.py
  ├── scraper/test_hackernews.py
  ├── scraper/test_reddit.py
  ├── translator/test_zh.py
  └── refresher/test_refresh.py
```

The project is isolated from the root Astro application. It owns Python code, configuration, secrets, and ignored work artifacts under `pipeline/`. Collection writes the shared datasets under `data/reddit/` and `data/hackernews/`; analysis publishes validated Markdown under `reports/reddit/`; translation publishes Chinese overlays under `reports/reddit/zh/`.

## Collection pipelines

### Reddit

1. Load validated topic groups from `pipeline/config/scraper/reddit.yml`.
2. Install browser-exported cookies for `rdt-cli`.
3. Fetch posts from each configured subreddit with request jitter.
4. Deduplicate posts by Reddit ID across communities.
5. Fetch top comments for the most-discussed posts in each batch.
6. Atomically merge results into root-level daily JSON snapshots.
7. Run every six hours in GitHub Actions and commit changed snapshots.

### Hacker News

1. Load `show-hn` and `ask-hn` stream limits from
   `pipeline/config/scraper/hackernews.yml`.
2. Discover current stories through the official `showstories` and
   `askstories` Firebase feeds.
3. Fetch authoritative story bodies and direct comments from the official item
   API.
4. Normalize HN HTML into plain text while retaining outbound link
   destinations.
5. Fetch up to 12 direct comments for the 12 most-discussed stories per stream.
6. Atomically merge a two-day UTC lookback into `data/hackernews/`.
7. For explicit historical dates, use Algolia only to discover IDs, then fetch
   the items from Firebase.
8. Run every six hours, ten minutes after Reddit collection.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)
- [`rdt-cli`](https://pypi.org/project/rdt-cli/)
- GitHub Copilot CLI for generated reports
- `ffmpeg` for six-frame Reddit video contact sheets
- Chromium installed through Playwright for cookie refreshes
- A Reddit session cookie exported from a browser account that may access the configured public communities

Hacker News collection uses public APIs and requires no secret.

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

From the repository root, run either collector. Config, data, and environment defaults are anchored to the repository layout rather than the current working directory:

```bash
uv run --project pipeline scrape-reddit
uv run --project pipeline scrape-reddit --name saas-build
uv run --project pipeline scrape-reddit --data-dir /tmp/reddit-ideas
uv run --project pipeline scrape-reddit --comments 0
uv run --project pipeline scrape-hackernews
uv run --project pipeline scrape-hackernews --name show-hn
uv run --project pipeline scrape-hackernews --date 2026-09-23
uv run --project pipeline scrape-hackernews --data-dir /tmp/hackernews
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
    max_posts_by_subreddit:
      microsaas: 12
      selfhosted: 12
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
| `max_posts_by_subreddit` | no | Per-community overrides for `max_posts`; every key must name a configured subreddit and every value must be 1-100 |
| `comments` | no | Top comments requested per qualifying post, 0-100; default 0 |
| `comment_percentile` | no | Discussion percentile used to select posts for comments; default 75 |

Source selection is based on downstream report citation yield rather than raw post volume. The current mix retains consistently productive communities such as `SideProject`, `SaaS`, `sysadmin`, `smallbusiness`, `indiehackers`, and `Entrepreneur`; removes low-yield `freelancers`, `productivity`, and `SomebodyMakeThis`; and trials practitioner-heavy `selfhosted`, `alphaandbetausers`, `shopify`, and `msp` at reduced quotas. The result covers 18 communities instead of 17 while reducing the configured per-run request ceiling from 400 to 274 posts (31.5%). Per-community caps keep the added coverage from expanding the corpus and Copilot context as if every source had equal demonstrated value.

A failed community does not discard successful results from others. A Reddit rate limit stops remaining requests and preserves the partial batch.

Hacker News stream settings are intentionally small:

```yaml
streams:
  - name: show-hn
    feed: showstories
    search_tag: show_hn
    max_items: 30
    comments: 12
    comment_story_limit: 12
```

| Field | Required | Description |
|---|---:|---|
| `name` | yes | Safe unique output-folder name |
| `feed` | yes | Official current feed: `showstories` or `askstories` |
| `search_tag` | yes | Matching Algolia historical tag: `show_hn` or `ask_hn` |
| `max_items` | no | Maximum stories discovered per run, 1-100; default 30 |
| `comments` | no | Maximum direct comments fetched per selected story, 0-50; default 12 |
| `comment_story_limit` | no | Most-discussed stories eligible for comment fetching, 0-50; default 12 |

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

HN snapshots are stored at `data/hackernews/<stream>/<YYYY-MM-DD>.json`.
They use the same post-shaped fields consumed by the analyzer plus explicit
`source`, `stream`, `time`, and `created_at` provenance:

```json
{
  "source": "hackernews",
  "stream": "show-hn",
  "last_fetched": "2026-09-23T20:25:10+00:00",
  "posts": [
    {
      "source": "hackernews",
      "stream": "show-hn",
      "id": "49816840",
      "title": "Show HN: Example",
      "score": 42,
      "num_comments": 6,
      "permalink": "https://news.ycombinator.com/item?id=49816840",
      "url": "https://example.com",
      "comments_data": [
        {
          "id": "49816841",
          "author": "reader",
          "body": "Direct comment text",
          "score": 0
        }
      ]
    }
  ]
}
```

HN exposes story points but no public per-comment score, so normalized HN
comments use score `0`. The pilot captures direct comments only; nested threads
are intentionally excluded to bound requests, context, and cost.

## Analysis pipeline

The analyzer combines three required Reddit streams with any same-date optional HN streams into static Markdown:

1. Discover exact-date sets containing `customer-pain`, `startup-ideas`, and `saas-build`, then append available `show-hn` and `ask-hn` snapshots.
2. Exclude today's still-changing files during automatic discovery and skip incomplete dates.
3. Skip dates that already have a full report unless `--force` is used.
4. Rank each stream independently by evidence richness using capped logarithmic engagement, detailed text/comments, quantified signals, concrete problems, and observed outcomes; thin viral posts receive a penalty.
5. Write per-stream review sets and dossiers plus full-corpus `external-links.json`, `media-manifest.json`, source hashes, and combined metadata under ignored `pipeline/artifacts/reddit/builder-intelligence/<date>/`. Metadata records each stream's platform. Up to seven earlier snapshots are represented by compact summaries of their six strongest evidence items rather than copied in full. Unambiguous domains in titles/text—including common `domain dot tld` spellings—are normalized to HTTPS manifest entries; common source-code and data filenames are excluded. HN discussion permalinks are classified as provenance rather than direct project candidates.
6. During generation only, safely download approved Reddit/Imgur images, normalize animated GIF/WebP and other unsupported image formats to one representative static PNG frame, and turn accessible Reddit DASH videos into six-frame contact sheets. Copilot receives only JPEG/PNG attachments; galleries, external videos, failures, and skipped items retain explicit URL/status records.
7. Attach every materialized visual to one sandboxed Copilot CLI process per date, with bounded worker concurrency, shell access disabled, built-in GitHub MCP disabled, and unrelated pipeline credentials removed.
8. Merge projects, founder validation, launches/metrics, failures, and useful visual findings into one case-level evidence ledger so each project or experiment has one primary home. Section 2 uses one structured `###` subsection per case rather than a wide table; direct images appear as linked Markdown images inside the matching case. Keep customer problems separate, then add short pattern synthesis and an action/watchlist section.
9. Require `media-review.json` to account for every detected media item and distinguish inspected, non-substantive, and unavailable assets. Deterministic `media_type` and `report_included` fields are normalized from the manifest and final report before validation. Focused attachment-repair batches are merged into the existing ledger so a model response containing only the current batch cannot discard previously reviewed media, and omitted batch items receive one focused retry.
10. Normalize common heading, field-label, stage, whitespace, and grounded schemeless-URL drift before validation. Accept an otherwise identical HTTP-to-HTTPS source-link upgrade, convert grounded inline-code media URLs into links, and turn direct image links in `Visual proof` fields into clickable image embeds. External destinations absent from the source manifests become non-clickable while retaining their descriptive text. Then block reports with missing core sections, incomplete or oversized case subsections, missing required tables, local paths, remaining unknown source URLs, unknown Reddit IDs/media, missing a current HN citation when HN was supplied, missing inspected-image embeds, insecure embedded images, or malformed review data. Treat exact table labels, per-section current-snapshot citation coverage, the eight-project target, inspected-video coverage, and source-derived HTTP hyperlinks as visible quality warnings rather than publication failures.
11. If Copilot exits nonzero after writing a candidate (for example, a native-binary crash), run the same strict normalization and validation before discarding it. A complete candidate is published with the CLI failure recorded as a warning; an incomplete candidate remains blocked.
12. Atomically publish valid output to `reports/reddit/<date>.md`. Each sandbox records `generation-metadata.json` with the selected model, effort, elapsed time, exit status, context size, attachments, and post count. The workflow commits validated reports even when a sibling date fails, then retains failed-run candidates, source-set metadata, ledgers, prompts, metadata, and logs as a seven-day diagnostics artifact.

Raw snapshots are never rewritten. A failed generation or validation leaves any existing published report untouched.

Install and authenticate Copilot CLI locally, or set `COPILOT_GITHUB_TOKEN` in `pipeline/.env`. The GitHub workflow maps the repository secret named `COPILOT_PAT` to that environment variable.

From the repository root:

```bash
uv run --project pipeline analyze-reddit --prepare-only
uv run --project pipeline analyze-reddit
uv run --project pipeline analyze-reddit --date 2026-08-02 --prepare-only
uv run --project pipeline analyze-reddit --include-today --workers 1
uv run --project pipeline analyze-reddit --limit 2
uv run --project pipeline analyze-reddit --date 2026-08-02 --force
```

`--date` may be repeated, but every selected date must contain all three required Reddit topic snapshots. HN remains optional. Explicit dates are never capped. Automatic discovery processes the newest missing report first and defaults to `--limit 1`, preventing an old backlog from repeatedly consuming every scheduled run. An explicit date may select today's snapshot, while `--include-today` only changes automatic discovery. `--prepare-only` always refreshes the combined manifests and sandbox per selected date, but never downloads media or invokes Copilot.

Model controls default to `--model gpt-5.4-mini --effort medium`. At the published rates used for this optimization, GPT-5.4 mini costs $0.75/M input tokens and $4.50/M output tokens versus Grok 4.5 at $2/M input and $6/M output, so it is approximately 62.5% cheaper on input and 25% cheaper on output. The Actions workflow retries a failed low-cost generation once with `grok-4.5` at high effort; valid first-pass reports are never regenerated by the fallback. Higher-cost models remain available through explicit model selection. Check current Copilot model pricing before revising this routing policy.

The repository-local skill at `.agents/skills/reddit-idea-analysis/SKILL.md` defines what counts as a valuable project, pain point, validation case, launch result, and visual finding across Reddit and Hacker News. Its five-section report starts with a short bottom line, five highlighted signals, and explicit coverage caveats; consolidates projects, experiments, outcomes, failures, and inspected media into one evidence ledger made of readable case subsections; keeps customer problems in a compact comparison table; and reserves the final sections for non-repetitive pattern synthesis, practical moves, and watch triggers. The report still maps convergence, partial support, contradictions, and missing links without opportunity scores or pretending unrelated posts form a tracked funnel. Keep downloaded media, contact sheets, model logs, metadata, and review ledgers in ignored `pipeline/artifacts/`; only validated reports are versioned.

Report schema migrations do not preserve legacy rendered formats. Delete outdated reports and overlays, then regenerate them locally from the collected raw snapshots under the current contract before publishing.

Validation is intentionally stricter than Astro's Markdown parser but no longer treats every quality target as fatal. The hard gates protect publishability, source provenance, and accidental data exposure; advisory warnings preserve useful partial reports while making coverage gaps visible in Actions logs and `validation-warnings.json`. Every workflow run retains context-size and generation telemetry for 30 days; failed runs additionally upload the generated report, review ledger, manifests, validation output, and Copilot logs as a seven-day diagnostics artifact.

The HN workflow runs at minute 27 every six hours, after Reddit's minute-17 collection and before the 02:43 UTC analyzer. `.github/workflows/analyze_reddit.yml` processes only the newest missing report by default and supports manual date, limit, model, effort, worker, force, include-today, and prepare-only inputs.

## Translation pipeline

Each published English report gets one Simplified Chinese overlay at `reports/reddit/zh/<date>.md`.

1. Discover published reports under `reports/reddit/`, newest first.
2. Queue a date when its overlay is missing, when the overlay's recorded `source_sha256` no longer matches the English report, or when it predates the current translation quality contract. This lets scheduled runs upgrade older overlays gradually instead of leaving historical website content on a weaker prompt forever.
3. Write a sandbox per date containing the exact `source.md`, a hedge-tokenized
   `translation-source.md`, `hedge-placeholders.json`, `structure.json`,
   `protected-terms.json`, the translation skill as `instructions.md`, and `prompt.txt`.
4. Run one sandboxed Copilot CLI process per date with shell access disabled, built-in GitHub MCP disabled, web access withheld, and unrelated pipeline credentials removed.
5. Normalize and validate the first pass before editing it. Hard checks cover the Markdown contract, exact evidence, uncertainty qualifiers, links, images, metrics, names, and technical identifiers. Schema values such as `Idea`, `Usage`, `Revenue`, `Abandoned`, `Unknown`, and Visual proof `None` are translated deterministically because they are report prose rather than technical identifiers.
6. Run a source-anchored GPT-6 Luna editor over the valid draft. The editor rewrites English-shaped clauses, bureaucratic padding, and literal terminology while rechecking every block against the English source. If its output fails hard validation, one focused repair is attempted; an invalid edited result is rejected and the already validated draft is restored.
7. Publish only a valid candidate with front matter recording `lang`, `source`, `source_sha256`, `quality_version`, the translation `model`, accepted `editor_model`, and `translated_at`. Generation, editor, and repair attempts record duration, exit status, and Copilot CLI token/cache usage in their metadata files.

```bash
uv run --project pipeline translate-zh --prepare-only
uv run --project pipeline translate-zh
uv run --project pipeline translate-zh --date 2026-08-05 --force
uv run --project pipeline translate-zh --limit 2 --model gpt-6-sol --effort high
```

Model controls default to `--model gpt-6-luna --effort medium`, `--workers 1`, and `--limit 5`; the source-anchored editor also uses GPT-6 Luna. GitHub lists GPT-6 Luna at $0.10/M input, $0.01/M cached input, $0.125/M cache writes, and $0.50/M output tokens, versus Gemini 3.8 Flash promotional pricing of $0.75/M input and $3.75/M output. In the repository benchmark, Luna translation plus Luna editing produced the best aggregate blind-review result while costing about one fifth to one tenth as much as the tested GPT-6 Sol routes, depending on report length and agent behavior. Gemini produced no candidate in more than 18 minutes on the same report, and Grok 4.7 took over 12 minutes with a more colloquial result. The Actions workflow retries a failed Luna run once with GPT-6 Sol; deterministic normalization and validation remain model-independent.

Each generation, editing, or focused-repair subprocess has a 20-minute timeout. A generation timeout fails the job explicitly; an editor timeout keeps the already validated draft instead of blocking publication indefinitely.

The skill at `.agents/skills/translate-zh/SKILL.md` carries the quality bar: recover the topic/action/result/limitation of each block before rewriting it, restructure English clause chains into short Chinese clauses, strip translationese and bureaucratic abstractions, apply a fixed builder-domain glossary, keep product names, URLs, metrics, and Reddit post titles verbatim, and preserve every hedge.

Blocking validation covers heading sequence and levels, the exact Section 2 case sequence and standard field labels, standard native-Chinese report headings and executive-highlight labels, per-table column and row counts, exact link and linked-image sets, protected metrics/code/subreddits/project names, Reddit title labels, uncertainty qualifiers, untranslated table headers, high-confidence translationese, and a Chinese-character floor that catches a mostly-English candidate. Softer style signals remain advisory warnings in `validation-warnings.json`. Every workflow run retains generation telemetry for 30 days; failed runs additionally upload the candidate, contract, prompt, validation output, and Copilot logs as a seven-day diagnostics artifact.

The workflow `.github/workflows/translate_zh.yml` starts when `Build intelligence report` completes, avoiding the former fixed-time race with long analysis runs. A 06:17 UTC recovery schedule handles missed or delayed completion events. Manual runs support date, limit, model, effort, worker, force, and prepare-only inputs.

## Cookie refresh pipeline

Reddit authentication here is browser-cookie based, so the pipeline renews the `REDDIT_COOKIES` session rather than exchanging an OAuth refresh token:

1. Read the current `REDDIT_COOKIES` Actions secret.
2. Launch Chromium with those cookies and visit Reddit.
3. Wait and scroll so Reddit can renew session cookies.
4. Capture the resulting Reddit cookie list and a screenshot.
5. Refuse to continue if Chromium returns no cookies.
6. Encrypt the new JSON with GitHub's repository public key.
7. Replace `REDDIT_COOKIES` through the GitHub Actions Secrets API.
8. If the refresh fails, create a GitHub issue containing the status/log report. Successful runs remain in Actions only; screenshots are retained as workflow artifacts for seven days.

Refresh settings live in `pipeline/config/refresher/reddit.yml`. The scheduled workflow at `.github/workflows/refresh_reddit_cookies.yml` runs at 01:23 UTC every third day and also supports manual dispatch.

### Bootstrap GitHub secrets

Configure these repository Actions secrets:

| Secret | Purpose |
|---|---|
| `REDDIT_COOKIES` | Playwright/browser-export JSON used by both collection and refresh workflows |
| `GH_PAT` | GitHub token permitted to update this repository's Actions secrets and create failure issues |
| `COPILOT_PAT` | Copilot CLI authentication used by the daily report-analysis and translation workflows |

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
