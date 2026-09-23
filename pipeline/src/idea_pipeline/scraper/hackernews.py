"""Collect Show HN and Ask HN stories into immutable daily JSON snapshots."""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Literal

import requests
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT, setup_logging

logger = logging.getLogger("idea_pipeline.scraper.hackernews")

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "scraper" / "hackernews.yml"
DEFAULT_DATA_DIR = REPOSITORY_ROOT / "data" / "hackernews"
FIREBASE_ROOT = "https://hacker-news.firebaseio.com/v0"
ALGOLIA_SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"
REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 3
USER_AGENT = "idea-pipeline/0.1 (+https://github.com/vibewatch/idea)"


class HackerNewsStream(BaseModel):
    """Validated settings for one Hacker News evidence stream."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    feed: Literal["showstories", "askstories"]
    search_tag: Literal["show_hn", "ask_hn"]
    max_items: int = Field(default=30, ge=1, le=100)
    comments: int = Field(default=12, ge=0, le=50)
    comment_story_limit: int = Field(default=12, ge=0, le=50)


class _TextExtractor(HTMLParser):
    """Convert HN's small HTML subset to readable text while retaining link URLs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p"}:
            self.parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            self.links.append(href or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.links:
            href = self.links.pop()
            if href:
                self.parts.append(f" ({href})")
        if tag == "p":
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: Any) -> str:
    """Return readable plain text from an HN story or comment body."""
    parser = _TextExtractor()
    parser.feed(str(value or ""))
    parser.close()
    text = html.unescape("".join(parser.parts))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


def load_config(config_path: str | Path = DEFAULT_CONFIG) -> list[HackerNewsStream]:
    """Load and validate Hacker News stream definitions."""
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
    unknown = set(raw) - {"streams"}
    if unknown:
        raise ValueError(
            f"Unknown top-level config field(s) in {path}: "
            + ", ".join(sorted(str(value) for value in unknown))
        )
    entries = raw.get("streams", [])
    if not isinstance(entries, list):
        raise TypeError(f"'streams' in {path} must be a list")

    streams: list[HackerNewsStream] = []
    names: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            stream = HackerNewsStream.model_validate(entry)
        except ValidationError as exc:
            name = entry.get("name", f"index {index}") if isinstance(entry, Mapping) else index
            raise ValueError(f"Invalid config for stream '{name}' in {path}:\n{exc}") from exc
        if stream.name in names:
            raise ValueError(f"Duplicate stream name '{stream.name}' in {path}")
        names.add(stream.name)
        streams.append(stream)
    return streams


def _request_json(
    session: requests.Session,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
) -> Any:
    """Fetch JSON with bounded retries for transient HN or network failures."""
    last_error: Exception | None = None
    for attempt in range(REQUEST_RETRIES):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            last_error = exc
            if attempt + 1 < REQUEST_RETRIES:
                time.sleep(2**attempt)
    raise RuntimeError(f"Request failed after {REQUEST_RETRIES} attempts: {url}: {last_error}")


def fetch_feed_ids(
    stream: HackerNewsStream,
    session: requests.Session,
) -> list[int]:
    """Fetch the current official HN feed IDs for a configured stream."""
    payload = _request_json(session, f"{FIREBASE_ROOT}/{stream.feed}.json")
    if not isinstance(payload, list):
        raise TypeError(f"HN feed {stream.feed} did not return a list")
    return [int(value) for value in payload[: stream.max_items] if str(value).isdigit()]


def fetch_date_ids(
    stream: HackerNewsStream,
    snapshot_date: date,
    session: requests.Session,
) -> list[int]:
    """Discover one UTC day's stream IDs through the public Algolia HN index."""
    start = int(datetime.combine(snapshot_date, datetime.min.time(), tzinfo=UTC).timestamp())
    end = int(
        datetime.combine(snapshot_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC).timestamp()
    )
    payload = _request_json(
        session,
        ALGOLIA_SEARCH_URL,
        params={
            "tags": stream.search_tag,
            "numericFilters": f"created_at_i>={start},created_at_i<{end}",
            "hitsPerPage": stream.max_items,
        },
    )
    hits = payload.get("hits") if isinstance(payload, Mapping) else None
    if not isinstance(hits, list):
        raise TypeError(f"Algolia HN search for {stream.name} did not return hits")
    ids: list[int] = []
    for hit in hits:
        object_id = hit.get("objectID") if isinstance(hit, Mapping) else None
        if object_id is not None and str(object_id).isdigit():
            ids.append(int(object_id))
    return ids


def fetch_item(item_id: int, session: requests.Session) -> dict[str, Any] | None:
    """Fetch one official HN item, returning None for deleted or unavailable items."""
    payload = _request_json(session, f"{FIREBASE_ROOT}/item/{item_id}.json")
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise TypeError(f"HN item {item_id} did not return an object")
    if payload.get("deleted") or payload.get("dead"):
        return None
    return dict(payload)


def fetch_comments(
    story: Mapping[str, Any],
    *,
    limit: int,
    session: requests.Session,
) -> list[dict[str, Any]]:
    """Fetch a bounded set of direct HN comments in source order."""
    children = story.get("kids")
    if limit <= 0 or not isinstance(children, list):
        return []
    comments: list[dict[str, Any]] = []
    for value in children:
        if len(comments) >= limit:
            break
        if not str(value).isdigit():
            continue
        item = fetch_item(int(value), session)
        if not item or item.get("type") != "comment":
            continue
        body = html_to_text(item.get("text"))
        if not body:
            continue
        comments.append(
            {
                "id": str(item.get("id") or value),
                "author": item.get("by"),
                "body": body,
                "score": 0,
                "time": item.get("time"),
                "parent": str(item.get("parent") or story.get("id") or ""),
            }
        )
    return comments


def normalize_story(
    item: Mapping[str, Any],
    stream: HackerNewsStream,
) -> dict[str, Any] | None:
    """Normalize an HN story to the shared post-shaped evidence contract."""
    if item.get("type") != "story" or item.get("id") is None or item.get("time") is None:
        return None
    item_id = str(item["id"])
    source_url = f"https://news.ycombinator.com/item?id={item_id}"
    outbound_url = str(item.get("url") or "").strip()
    return {
        "source": "hackernews",
        "stream": stream.name,
        "id": item_id,
        "title": html_to_text(item.get("title")),
        "subreddit": stream.name,
        "author": item.get("by"),
        "score": max(0, int(item.get("score") or 0)),
        "num_comments": max(0, int(item.get("descendants") or 0)),
        "permalink": source_url,
        "url": outbound_url or source_url,
        "selftext": html_to_text(item.get("text")),
        "is_self": not bool(outbound_url),
        "is_video": False,
        "time": int(item["time"]),
        "created_at": datetime.fromtimestamp(int(item["time"]), UTC).isoformat(),
    }


def _read_existing_posts(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Existing snapshot is invalid JSON: {path}") from exc
    posts = payload.get("posts") if isinstance(payload, Mapping) else None
    if not isinstance(posts, list) or not all(isinstance(post, Mapping) for post in posts):
        raise ValueError(f"Existing snapshot has an invalid posts collection: {path}")
    return [dict(post) for post in posts]


def merge_posts(
    path: Path,
    posts: Sequence[Mapping[str, Any]],
    *,
    stream: str,
    fetched_at: datetime | None = None,
) -> int:
    """Atomically merge normalized HN stories into one UTC-day snapshot."""
    by_id: dict[str, dict[str, Any]] = {}
    for post in [*_read_existing_posts(path), *posts]:
        item_id = str(post.get("id") or "").strip()
        if not item_id:
            raise ValueError("Cannot persist a Hacker News story without an id")
        incoming = dict(post)
        incoming["id"] = item_id
        previous = by_id.get(item_id)
        if previous and previous.get("comments_data") and not incoming.get("comments_data"):
            incoming["comments_data"] = previous["comments_data"]
        by_id[item_id] = incoming

    output = {
        "source": "hackernews",
        "stream": stream,
        "last_fetched": (fetched_at or datetime.now(UTC)).isoformat(),
        "posts": sorted(
            by_id.values(),
            key=lambda post: (-int(post.get("time") or 0), str(post.get("id") or "")),
        ),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(by_id)


def collect_stream(
    stream: HackerNewsStream,
    *,
    data_dir: Path,
    session: requests.Session,
    dates: Sequence[date] | None = None,
    lookback_days: int = 2,
    now: datetime | None = None,
) -> int:
    """Collect one stream and return the number of snapshots updated."""
    current = now or datetime.now(UTC)
    requested_dates = tuple(dict.fromkeys(dates or ()))
    if requested_dates:
        ids = [
            item_id
            for snapshot_date in requested_dates
            for item_id in fetch_date_ids(stream, snapshot_date, session)
        ]
        allowed_dates = set(requested_dates)
    else:
        ids = fetch_feed_ids(stream, session)
        earliest = current.date() - timedelta(days=max(lookback_days - 1, 0))
        allowed_dates = {
            earliest + timedelta(days=offset)
            for offset in range((current.date() - earliest).days + 1)
        }

    stories: list[dict[str, Any]] = []
    source_items: dict[str, dict[str, Any]] = {}
    seen: set[int] = set()
    for item_id in ids:
        if item_id in seen:
            continue
        seen.add(item_id)
        item = fetch_item(item_id, session)
        if not item:
            continue
        story = normalize_story(item, stream)
        if not story:
            continue
        story_date = datetime.fromtimestamp(int(story["time"]), UTC).date()
        if story_date in allowed_dates:
            stories.append(story)
            source_items[str(story["id"])] = item

    comment_candidates = sorted(
        stories,
        key=lambda story: (-int(story.get("num_comments") or 0), -int(story.get("score") or 0)),
    )[: stream.comment_story_limit]
    story_by_id = {str(story["id"]): story for story in stories}
    for story in comment_candidates:
        story_id = str(story["id"])
        comments = fetch_comments(
            source_items[story_id],
            limit=stream.comments,
            session=session,
        )
        if comments:
            story_by_id[story_id]["comments_data"] = comments

    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for story in stories:
        story_date = datetime.fromtimestamp(int(story["time"]), UTC).date()
        by_date[story_date].append(story)

    updated = 0
    for snapshot_date, date_stories in sorted(by_date.items()):
        path = data_dir / stream.name / f"{snapshot_date.isoformat()}.json"
        total = merge_posts(path, date_stories, stream=stream.name, fetched_at=current)
        logger.info(
            "[%s] merged %d fetched stories into %s (%d total)",
            stream.name,
            len(date_stories),
            path,
            total,
        )
        updated += 1
    if not by_date:
        logger.info("[%s] no stories matched the selected UTC date range", stream.name)
    return updated


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _date_value(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape-hackernews",
        description="Collect Show HN and Ask HN into daily JSON snapshots",
    )
    parser.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("-d", "--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--date", action="append", dest="dates", type=_date_value)
    parser.add_argument("--lookback-days", type=_positive_int, default=2)
    parser.add_argument("--name", help="Run only the named stream")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    try:
        streams = load_config(args.config)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        logger.error("%s", exc)
        return 2
    if args.name:
        streams = [stream for stream in streams if stream.name == args.name]
        if not streams:
            logger.error("Stream '%s' not found in config", args.name)
            return 2
    if not streams:
        logger.info("No Hacker News streams configured")
        return 0

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    failures = 0
    for stream in streams:
        try:
            collect_stream(
                stream,
                data_dir=args.data_dir,
                session=session,
                dates=args.dates,
                lookback_days=args.lookback_days,
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.error("[%s] collection failed: %s", stream.name, exc)
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
