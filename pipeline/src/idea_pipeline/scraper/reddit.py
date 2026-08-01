"""Collect Reddit posts and comments into deduplicated daily JSON snapshots.

The collector delegates Reddit access to ``rdt-cli``. It validates YAML monitor
configuration, installs cookies from the environment, handles partial/rate-limited
runs, enriches highly discussed posts with comments, and atomically merges output.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import random
import re
import shlex
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT, setup_logging

logger = logging.getLogger("idea_pipeline.scraper.reddit")

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "scraper" / "reddit.yml"
DEFAULT_DATA_DIR = REPOSITORY_ROOT / "data" / "reddit"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
COOKIES_ENV = "REDDIT_COOKIES"
REQUEST_DELAY = (2.0, 5.0)
DEFAULT_COMMENT_PERCENTILE = 75.0
BOT_AUTHORS: frozenset[str] = frozenset(
    {
        "AutoModerator",
        "RemindMeBot",
        "WikiSummarizerBot",
        "SaveVideo",
        "RepostSleuthBot",
        "bot-b0t",
    }
)
SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]{2,21}$")


class RedditMonitor(BaseModel):
    """Validated settings for one logical Reddit collection topic."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, validate_assignment=True)

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    subreddits: list[str] = Field(min_length=1)
    sort: Literal["hot", "new", "top", "rising", "controversial", "best"] = "hot"
    time: Literal["hour", "day", "week", "month", "year", "all"] = "day"
    max_posts: int = Field(default=25, ge=1, le=100)
    comments: int = Field(default=0, ge=0, le=100)
    comment_percentile: float = Field(default=DEFAULT_COMMENT_PERCENTILE, ge=0, le=100)

    @model_validator(mode="before")
    @classmethod
    def _normalize_subreddits(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        normalized = dict(data)
        if "subreddit" in normalized and "subreddits" in normalized:
            raise ValueError("use either 'subreddit' or 'subreddits', not both")
        if "subreddit" in normalized:
            raw = normalized.pop("subreddit")
            normalized["subreddits"] = raw if isinstance(raw, list) else [raw]
        return normalized

    @field_validator("subreddits")
    @classmethod
    def _validate_subreddits(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            subreddit = value.strip()
            if subreddit.lower().startswith("r/"):
                subreddit = subreddit[2:]
            if not SUBREDDIT_PATTERN.fullmatch(subreddit):
                raise ValueError(
                    f"invalid subreddit '{value}'; use only its 2-21 character name"
                )
            key = subreddit.casefold()
            if key not in seen:
                normalized.append(subreddit)
                seen.add(key)
        return normalized


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> list[RedditMonitor]:
    """Load and validate monitor definitions from YAML."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        raise TypeError(f"Config root in {path} must be a mapping")

    unknown_keys = set(raw) - {"monitors"}
    if unknown_keys:
        names = ", ".join(sorted(str(key) for key in unknown_keys))
        raise ValueError(f"Unknown top-level config field(s) in {path}: {names}")

    entries = raw.get("monitors", [])
    if not isinstance(entries, list):
        raise TypeError(f"'monitors' in {path} must be a list")

    monitors: list[RedditMonitor] = []
    names: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            monitor = RedditMonitor.model_validate(entry)
        except ValidationError as exc:
            name = entry.get("name", f"index {index}") if isinstance(entry, Mapping) else index
            raise ValueError(f"Invalid config for monitor '{name}' in {path}:\n{exc}") from exc
        if monitor.name in names:
            raise ValueError(f"Duplicate monitor name '{monitor.name}' in {path}")
        names.add(monitor.name)
        monitors.append(monitor)
    return monitors


def setup() -> None:
    """Write Reddit cookies from ``REDDIT_COOKIES`` to rdt-cli's credential file."""
    cookies_json = os.environ.get(COOKIES_ENV)
    if not cookies_json:
        raise RuntimeError(f"{COOKIES_ENV} environment variable is not set")

    try:
        raw: Any = json.loads(cookies_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{COOKIES_ENV} must contain valid JSON: {exc.msg}") from exc

    credential: dict[str, Any]
    if isinstance(raw, list):
        cookie_map: dict[str, str] = {}
        for index, cookie in enumerate(raw):
            if not isinstance(cookie, Mapping) or "name" not in cookie or "value" not in cookie:
                raise RuntimeError(
                    f"{COOKIES_ENV} cookie at index {index} must contain name and value"
                )
            cookie_map[str(cookie["name"])] = str(cookie["value"])
        credential = {"cookies": cookie_map}
    elif isinstance(raw, dict):
        credential = dict(raw) if "cookies" in raw else {"cookies": raw}
    else:
        raise TypeError(f"{COOKIES_ENV} must be a cookie list or credential object")

    cookies = credential.get("cookies")
    if not isinstance(cookies, dict) or not cookies:
        raise RuntimeError(f"{COOKIES_ENV} does not contain any cookies")

    cookie_dir = Path.home() / ".config" / "rdt-cli"
    cookie_dir.mkdir(parents=True, exist_ok=True)
    cookie_file = cookie_dir / "credential.json"
    cookie_file.write_text(json.dumps(credential, indent=2), encoding="utf-8")
    cookie_file.chmod(0o600)
    logger.info("Auth configured from %s (%d cookies)", COOKIES_ENV, len(cookies))


def _is_rate_limited(stderr: str) -> bool:
    """Return whether command output indicates Reddit throttling."""
    lowered = stderr.lower()
    return "429" in lowered or "rate limit" in lowered or "too many requests" in lowered


def _random_delay() -> None:
    """Apply jitter between requests to avoid bursts."""
    time.sleep(random.uniform(*REQUEST_DELAY))


def _as_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def build_command(subreddit: str, monitor: RedditMonitor) -> list[str]:
    """Build the rdt-cli post command for one subreddit."""
    command = [
        "rdt",
        "sub",
        subreddit,
        "-s",
        monitor.sort,
        "-n",
        str(monitor.max_posts),
        "--json",
        "--compact",
    ]
    if monitor.sort in ("top", "controversial"):
        command.extend(["-t", monitor.time])
    return command


def extract_posts(raw: Any) -> list[dict[str, Any]]:
    """Extract post objects from the common rdt-cli response shapes."""
    data = raw.get("data", raw) if isinstance(raw, Mapping) else raw
    if isinstance(data, Mapping):
        inner = data.get("data", data)
        if isinstance(inner, Mapping) and isinstance(inner.get("children"), list):
            posts: list[dict[str, Any]] = []
            for child in inner["children"]:
                post = child.get("data", child) if isinstance(child, Mapping) else None
                if isinstance(post, Mapping):
                    posts.append(dict(post))
            return posts
    if isinstance(data, list):
        return [dict(post) for post in data if isinstance(post, Mapping)]
    return []


def deduplicate_posts(posts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate posts by Reddit ID, keeping the latest representation."""
    by_id: dict[str, dict[str, Any]] = {}
    for post in posts:
        post_id = post.get("id")
        if post_id is None or str(post_id).strip() == "":
            logger.warning("Skipping a Reddit post without an id")
            continue
        normalized = dict(post)
        normalized["id"] = str(post_id)
        by_id[normalized["id"]] = normalized
    return list(by_id.values())


def select_comment_candidates(
    posts: Sequence[Mapping[str, Any]],
    percentile: float = DEFAULT_COMMENT_PERCENTILE,
) -> tuple[list[dict[str, Any]], int | None]:
    """Select discussed posts at or above a comment-count percentile."""
    candidates = [dict(post) for post in posts if _as_nonnegative_int(post.get("num_comments")) > 0]
    if not candidates:
        return [], None

    bounded_percentile = min(max(percentile, 0), 100)
    counts = sorted(_as_nonnegative_int(post.get("num_comments")) for post in candidates)
    rank = max(0, math.ceil(len(counts) * (bounded_percentile / 100)) - 1)
    threshold = counts[min(rank, len(counts) - 1)]

    selected = [
        post
        for post in candidates
        if _as_nonnegative_int(post.get("num_comments")) >= threshold
    ]
    selected.sort(key=lambda post: _as_nonnegative_int(post.get("num_comments")), reverse=True)
    return selected, threshold


def fetch_comments(post_id: str, limit: int, min_score: int = 1) -> list[dict[str, Any]] | None:
    """Fetch top comments; return ``None`` specifically when rate limited."""
    command = ["rdt", "read", post_id, "-n", str(limit), "-s", "top", "--json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        logger.warning("Comment fetch timed out for post %s", post_id)
        return []
    except FileNotFoundError:
        logger.error("rdt-cli is not installed; install it with 'uv tool install rdt-cli'")
        return []

    if result.returncode != 0:
        if _is_rate_limited(result.stderr):
            return None
        logger.warning("Comment fetch failed for post %s: %s", post_id, result.stderr.strip())
        return []

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("rdt-cli returned invalid comment JSON for post %s", post_id)
        return []

    data = raw.get("data", []) if isinstance(raw, Mapping) else []
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], Mapping):
        return []

    children = data[1].get("data", {}).get("children", [])
    if not isinstance(children, list):
        return []

    comments: list[dict[str, Any]] = []
    for child in children:
        if not isinstance(child, Mapping) or child.get("kind") != "t1":
            continue
        comment = child.get("data", child)
        if not isinstance(comment, Mapping):
            continue
        author = comment.get("author")
        score = _as_nonnegative_int(comment.get("score"))
        if author in BOT_AUTHORS or score < min_score:
            continue
        comments.append(
            {
                "id": comment.get("id"),
                "author": author,
                "body": comment.get("body", ""),
                "score": score,
            }
        )
    return comments


def _read_existing_posts(daily_file: Path) -> list[dict[str, Any]]:
    if not daily_file.exists():
        return []
    try:
        raw = json.loads(daily_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Existing snapshot is invalid JSON: {daily_file}") from exc

    posts = raw if isinstance(raw, list) else raw.get("posts") if isinstance(raw, Mapping) else None
    if not isinstance(posts, list) or not all(isinstance(post, Mapping) for post in posts):
        raise ValueError(f"Existing snapshot has an invalid posts collection: {daily_file}")
    return [dict(post) for post in posts]


def merge_posts(daily_file: Path, new_posts: Sequence[Mapping[str, Any]]) -> int:
    """Atomically merge posts into a daily file, deduplicating by ID."""
    existing_posts = _read_existing_posts(daily_file)
    by_id: dict[str, dict[str, Any]] = {}

    for post in [*existing_posts, *new_posts]:
        post_id = post.get("id")
        if post_id is None or str(post_id).strip() == "":
            raise ValueError("Cannot persist a Reddit post without an id")
        key = str(post_id)
        incoming = dict(post)
        incoming["id"] = key
        previous = by_id.get(key)
        if previous and "comments_data" in previous and "comments_data" not in incoming:
            incoming["comments_data"] = previous["comments_data"]
        by_id[key] = incoming

    output = {
        "last_fetched": datetime.now(UTC).strftime("%Y-%m-%d"),
        "posts": list(by_id.values()),
    }
    daily_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = daily_file.with_name(f".{daily_file.name}.tmp")
    try:
        temporary_file.write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary_file.replace(daily_file)
    finally:
        temporary_file.unlink(missing_ok=True)
    return len(by_id)


def run(monitor: RedditMonitor, data_dir: Path) -> bool:
    """Run one configured monitor and persist any collected posts."""
    date_string = datetime.now(UTC).strftime("%Y-%m-%d")
    daily_file = data_dir / monitor.name / f"{date_string}.json"

    all_posts: list[dict[str, Any]] = []
    rate_limited = False
    for index, subreddit in enumerate(monitor.subreddits):
        if rate_limited:
            logger.warning("[%s] Skipping remaining subreddits (rate limited)", monitor.name)
            break
        if index > 0:
            _random_delay()

        command = build_command(subreddit, monitor)
        logger.info("[%s] Running: %s", monitor.name, shlex.join(command))
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=False
            )
        except subprocess.TimeoutExpired:
            logger.warning("[%s] r/%s timed out; skipping", monitor.name, subreddit)
            continue
        except FileNotFoundError:
            logger.error("rdt-cli is not installed; install it with 'uv tool install rdt-cli'")
            return False

        if result.returncode != 0:
            stderr = result.stderr.strip()
            if _is_rate_limited(stderr):
                logger.warning("[%s] Rate limited; preserving partial results", monitor.name)
                rate_limited = True
            else:
                logger.warning("[%s] r/%s failed: %s", monitor.name, subreddit, stderr)
            continue

        try:
            posts = extract_posts(json.loads(result.stdout))
        except json.JSONDecodeError:
            logger.warning("[%s] r/%s returned invalid JSON", monitor.name, subreddit)
            continue
        logger.info("[%s] Fetched %d posts from r/%s", monitor.name, len(posts), subreddit)
        all_posts.extend(posts)

    new_posts = deduplicate_posts(all_posts)
    if not new_posts:
        logger.info("[%s] No posts fetched", monitor.name)
        return rate_limited

    logger.info("[%s] Collected %d posts (%d unique)", monitor.name, len(all_posts), len(new_posts))

    if monitor.comments > 0:
        candidates, threshold = select_comment_candidates(
            new_posts, percentile=monitor.comment_percentile
        )
        if threshold is not None:
            logger.info(
                "[%s] Fetching up to %d comments for %d posts at/above %.1f percentile "
                "(%d+ comments)",
                monitor.name,
                monitor.comments,
                len(candidates),
                monitor.comment_percentile,
                threshold,
            )
        for index, candidate in enumerate(candidates):
            if index > 0:
                _random_delay()
            post_id = str(candidate["id"])
            fetch_limit = min(
                monitor.comments, _as_nonnegative_int(candidate.get("num_comments"))
            )
            comments = fetch_comments(post_id, limit=fetch_limit)
            if comments is None:
                logger.warning("[%s] Rate limited; stopping comment fetches", monitor.name)
                break
            if comments:
                candidate["comments_data"] = comments
                for post in new_posts:
                    if post["id"] == post_id:
                        post["comments_data"] = comments
                        break

    try:
        existing_count = len(_read_existing_posts(daily_file))
        total = merge_posts(daily_file, new_posts)
    except (OSError, ValueError) as exc:
        logger.error("[%s] Could not persist %s: %s", monitor.name, daily_file, exc)
        return False

    logger.info(
        "[%s] Merged %d fetched with %d existing -> %d total",
        monitor.name,
        len(new_posts),
        existing_count,
        total,
    )
    return True


def _nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-reddit",
        description="Collect configured Reddit communities into daily JSON snapshots",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help="Config file (default: pipeline/config/scraper/reddit.yml)",
    )
    parser.add_argument(
        "-d",
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Output directory (default: data/reddit)",
    )
    parser.add_argument(
        "--comments",
        type=_nonnegative_int,
        default=None,
        help="Override comments fetched per qualifying post (0 disables)",
    )
    parser.add_argument("--name", help="Run only the named monitor")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the Reddit collector CLI and return a process exit code."""
    args = build_parser().parse_args(argv)
    setup_logging()
    load_dotenv(DEFAULT_ENV_FILE)

    try:
        monitors = load_config(args.config)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        logger.error("%s", exc)
        return 2

    if args.name is not None:
        monitors = [monitor for monitor in monitors if monitor.name == args.name]
        if not monitors:
            logger.error("Monitor '%s' not found in config", args.name)
            return 2

    if not monitors:
        logger.info("No monitors configured")
        return 0

    if args.comments is not None:
        for monitor in monitors:
            monitor.comments = args.comments

    try:
        setup()
    except (RuntimeError, TypeError) as exc:
        logger.error("%s", exc)
        return 2

    logger.info("Running %d monitor(s)", len(monitors))
    results: dict[str, bool] = {}
    for index, monitor in enumerate(monitors):
        if index > 0:
            _random_delay()
        results[monitor.name] = run(monitor, args.data_dir)

    failed = [name for name, succeeded in results.items() if not succeeded]
    if failed:
        logger.error("Failed monitors: %s", ", ".join(failed))
        return 1
    logger.info("All monitors completed successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
