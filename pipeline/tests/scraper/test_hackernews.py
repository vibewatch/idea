"""Unit tests for the Hacker News signal collector."""

from __future__ import annotations

import json
import textwrap
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT
from idea_pipeline.scraper.hackernews import (
    ALGOLIA_SEARCH_URL,
    DEFAULT_CONFIG,
    DEFAULT_DATA_DIR,
    FIREBASE_ROOT,
    HackerNewsStream,
    collect_stream,
    fetch_comments,
    fetch_date_ids,
    fetch_feed_ids,
    html_to_text,
    load_config,
    main,
    merge_posts,
    normalize_story,
)


class TestPathsAndConfig:
    def test_default_paths_match_repository_layout(self) -> None:
        assert DEFAULT_CONFIG == PROJECT_ROOT / "config" / "scraper" / "hackernews.yml"
        assert DEFAULT_DATA_DIR == REPOSITORY_ROOT / "data" / "hackernews"

    def test_stream_defaults(self) -> None:
        stream = HackerNewsStream(name="show-hn", feed="showstories", search_tag="show_hn")

        assert stream.max_items == 30
        assert stream.comments == 12
        assert stream.comment_story_limit == 12

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "../escape"),
            ("feed", "topstories"),
            ("search_tag", "story"),
            ("max_items", 0),
            ("comments", 51),
            ("comment_story_limit", -1),
        ],
    )
    def test_rejects_invalid_stream_values(self, field: str, value: object) -> None:
        values: dict[str, object] = {
            "name": "show-hn",
            "feed": "showstories",
            "search_tag": "show_hn",
        }
        values[field] = value
        with pytest.raises(ValidationError):
            HackerNewsStream.model_validate(values)

    def test_loads_config_and_rejects_duplicate_names(self, tmp_path: Path) -> None:
        config = tmp_path / "hackernews.yml"
        config.write_text(
            textwrap.dedent(
                """\
                streams:
                  - name: show-hn
                    feed: showstories
                    search_tag: show_hn
                  - name: ask-hn
                    feed: askstories
                    search_tag: ask_hn
                """
            ),
            encoding="utf-8",
        )

        assert [stream.name for stream in load_config(config)] == ["show-hn", "ask-hn"]

        config.write_text(
            "streams:\n"
            "  - {name: show-hn, feed: showstories, search_tag: show_hn}\n"
            "  - {name: show-hn, feed: showstories, search_tag: show_hn}\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="Duplicate stream"):
            load_config(config)


class TestNormalization:
    def test_html_to_text_retains_link_destination(self) -> None:
        assert html_to_text('Try <a href="https://example.com">this tool</a><p>Next') == (
            "Try this tool (https://example.com)\nNext"
        )

    def test_normalizes_story_to_shared_contract(self) -> None:
        stream = HackerNewsStream(name="show-hn", feed="showstories", search_tag="show_hn")
        story = normalize_story(
            {
                "id": 123,
                "type": "story",
                "by": "builder",
                "time": 1_788_134_400,
                "title": "Show HN: Tool",
                "text": "Measured <b>result</b>",
                "url": "https://example.com/tool",
                "score": 42,
                "descendants": 9,
            },
            stream,
        )

        assert story is not None
        assert story["source"] == "hackernews"
        assert story["stream"] == "show-hn"
        assert story["permalink"] == "https://news.ycombinator.com/item?id=123"
        assert story["url"] == "https://example.com/tool"
        assert story["selftext"] == "Measured result"
        assert story["num_comments"] == 9


class TestApiQueries:
    @patch("idea_pipeline.scraper.hackernews._request_json")
    def test_fetches_bounded_feed_ids(self, mock_request: MagicMock) -> None:
        mock_request.return_value = [1, "2", "bad", 3]
        stream = HackerNewsStream(
            name="show-hn",
            feed="showstories",
            search_tag="show_hn",
            max_items=3,
        )

        assert fetch_feed_ids(stream, MagicMock()) == [1, 2]
        mock_request.assert_called_once_with(
            mock_request.call_args.args[0],
            f"{FIREBASE_ROOT}/showstories.json",
        )

    @patch("idea_pipeline.scraper.hackernews._request_json")
    def test_fetches_date_ids_from_algolia(self, mock_request: MagicMock) -> None:
        mock_request.return_value = {"hits": [{"objectID": "11"}, {"objectID": "bad"}]}
        stream = HackerNewsStream(name="ask-hn", feed="askstories", search_tag="ask_hn")

        assert fetch_date_ids(stream, date(2026, 9, 23), MagicMock()) == [11]
        assert mock_request.call_args.args[1] == ALGOLIA_SEARCH_URL
        assert mock_request.call_args.kwargs["params"]["tags"] == "ask_hn"

    @patch("idea_pipeline.scraper.hackernews.fetch_item")
    def test_fetches_direct_comments(self, mock_fetch: MagicMock) -> None:
        mock_fetch.side_effect = [
            {"id": 2, "type": "comment", "by": "reader", "text": "Useful", "parent": 1},
            {"id": 3, "type": "comment", "deleted": True},
        ]

        comments = fetch_comments(
            {"id": 1, "kids": [2, 3]},
            limit=5,
            session=MagicMock(),
        )

        assert comments == [
            {
                "id": "2",
                "author": "reader",
                "body": "Useful",
                "score": 0,
                "time": None,
                "parent": "1",
            }
        ]


class TestPersistenceAndCollection:
    def test_merge_preserves_existing_comments(self, tmp_path: Path) -> None:
        path = tmp_path / "show-hn" / "2026-09-23.json"
        old = {"id": "1", "time": 1, "comments_data": [{"id": "c1"}]}
        merge_posts(
            path,
            [old],
            stream="show-hn",
            fetched_at=datetime(2026, 9, 23, tzinfo=UTC),
        )

        total = merge_posts(
            path,
            [{"id": "1", "time": 2, "title": "updated"}],
            stream="show-hn",
            fetched_at=datetime(2026, 9, 23, 1, tzinfo=UTC),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        assert total == 1
        assert payload["source"] == "hackernews"
        assert payload["posts"][0]["comments_data"] == [{"id": "c1"}]

    @patch(
        "idea_pipeline.scraper.hackernews.fetch_comments",
        return_value=[
            {
                "id": "10",
                "author": "reader",
                "body": "Useful implementation detail",
                "score": 0,
                "time": 1,
                "parent": "1",
            }
        ],
    )
    @patch("idea_pipeline.scraper.hackernews.fetch_item")
    @patch("idea_pipeline.scraper.hackernews.fetch_feed_ids", return_value=[1, 2])
    def test_collects_only_dates_in_lookback(
        self,
        _mock_ids: MagicMock,
        mock_item: MagicMock,
        _mock_comments: MagicMock,
        tmp_path: Path,
    ) -> None:
        now = datetime(2026, 9, 23, 12, tzinfo=UTC)
        mock_item.side_effect = [
            {
                "id": 1,
                "type": "story",
                "by": "builder",
                "time": int(datetime(2026, 9, 23, 8, tzinfo=UTC).timestamp()),
                "title": "Show HN: Today",
                "descendants": 1,
                "kids": [10],
            },
            {
                "id": 2,
                "type": "story",
                "by": "builder",
                "time": int(datetime(2026, 9, 20, 8, tzinfo=UTC).timestamp()),
                "title": "Show HN: Old",
            },
        ]
        stream = HackerNewsStream(name="show-hn", feed="showstories", search_tag="show_hn")

        updated = collect_stream(
            stream,
            data_dir=tmp_path,
            session=MagicMock(),
            lookback_days=2,
            now=now,
        )

        assert updated == 1
        payload = json.loads((tmp_path / "show-hn" / "2026-09-23.json").read_text())
        assert [post["id"] for post in payload["posts"]] == ["1"]
        assert payload["posts"][0]["comments_data"][0]["id"] == "10"
        assert _mock_comments.call_args.args[0]["kids"] == [10]

    @patch("idea_pipeline.scraper.hackernews.collect_stream")
    def test_main_returns_failure_when_collection_fails(
        self, mock_collect: MagicMock, tmp_path: Path
    ) -> None:
        config = tmp_path / "config.yml"
        config.write_text(
            "streams:\n  - {name: show-hn, feed: showstories, search_tag: show_hn}\n",
            encoding="utf-8",
        )
        mock_collect.side_effect = RuntimeError("network unavailable")

        assert main(["--config", str(config), "--data-dir", str(tmp_path / "data")]) == 1
