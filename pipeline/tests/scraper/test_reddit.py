"""Unit tests for the Reddit idea scraper; all external commands are mocked."""

from __future__ import annotations

import json
import stat
import subprocess
import textwrap
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from pydantic import ValidationError

from idea_pipeline import PROJECT_ROOT, REPOSITORY_ROOT
from idea_pipeline.scraper.reddit import (
    DEFAULT_CONFIG,
    DEFAULT_DATA_DIR,
    DEFAULT_ENV_FILE,
    RedditMonitor,
    build_command,
    build_parser,
    deduplicate_posts,
    extract_posts,
    fetch_comments,
    load_config,
    main,
    merge_posts,
    run,
    select_comment_candidates,
    setup,
)


class TestProjectPaths:
    def test_app_and_repository_roots_match_monorepo_layout(self) -> None:
        expected_project_root = Path(__file__).resolve().parents[2]

        assert PROJECT_ROOT == expected_project_root
        assert REPOSITORY_ROOT == expected_project_root.parent
        assert DEFAULT_CONFIG == PROJECT_ROOT / "config" / "scraper" / "reddit.yml"
        assert DEFAULT_DATA_DIR == REPOSITORY_ROOT / "data" / "reddit"
        assert DEFAULT_ENV_FILE == PROJECT_ROOT / ".env"


class TestRedditMonitor:
    def test_defaults_and_subreddit_normalization(self) -> None:
        monitor = RedditMonitor(name="ideas", subreddits=["r/SaaS", "saas", "microsaas"])

        assert monitor.subreddits == ["SaaS", "microsaas"]
        assert monitor.sort == "hot"
        assert monitor.time == "day"
        assert monitor.max_posts == 25
        assert monitor.comments == 0
        assert monitor.comment_percentile == 75

    def test_singular_subreddit_alias(self) -> None:
        monitor = RedditMonitor.model_validate({"name": "ideas", "subreddit": "SaaS"})

        assert monitor.subreddits == ["SaaS"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "../escape"),
            ("sort", "old"),
            ("time", "forever"),
            ("max_posts", 0),
            ("comments", -1),
            ("comment_percentile", 101),
            ("subreddits", ["r/not-valid!"]),
        ],
    )
    def test_rejects_invalid_values(self, field: str, value: object) -> None:
        values: dict[str, object] = {"name": "ideas", "subreddits": ["SaaS"]}
        values[field] = value

        with pytest.raises(ValidationError):
            RedditMonitor.model_validate(values)

    def test_rejects_both_subreddit_fields(self) -> None:
        with pytest.raises(ValidationError, match="either 'subreddit' or 'subreddits'"):
            RedditMonitor.model_validate(
                {"name": "ideas", "subreddit": "SaaS", "subreddits": ["microsaas"]}
            )


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path: Path) -> None:
        config = tmp_path / "reddit.yml"
        config.write_text(
            textwrap.dedent(
                """\
                monitors:
                  - name: saas
                    subreddit: SaaS
                  - name: founders
                    subreddit:
                      - startups
                      - Entrepreneur
                    sort: top
                    time: week
                    max_posts: 50
                    comments: 10
                    comment_percentile: 60
                """
            ),
            encoding="utf-8",
        )

        monitors = load_config(config)

        assert [monitor.name for monitor in monitors] == ["saas", "founders"]
        assert monitors[0].subreddits == ["SaaS"]
        assert monitors[1].subreddits == ["startups", "Entrepreneur"]
        assert monitors[1].comment_percentile == 60

    def test_empty_document_is_empty_config(self, tmp_path: Path) -> None:
        config = tmp_path / "reddit.yml"
        config.write_text("", encoding="utf-8")

        assert load_config(config) == []

    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError, match="Config file not found"):
            load_config("/does/not/exist.yml")

    @pytest.mark.parametrize(
        "content",
        [
            "- not-a-mapping\n",
            "monitors: not-a-list\n",
            "other: value\n",
            "monitors:\n  - name: missing-subreddit\n",
            (
                "monitors:\n  - name: first\n    subreddit: SaaS\n"
                "  - name: first\n    subreddit: startups\n"
            ),
            "monitors: [\n",
        ],
    )
    def test_rejects_invalid_config(self, tmp_path: Path, content: str) -> None:
        config = tmp_path / "reddit.yml"
        config.write_text(content, encoding="utf-8")

        with pytest.raises((TypeError, ValueError)):
            load_config(config)


class TestSetup:
    def test_writes_list_cookies_with_private_permissions(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(
            "REDDIT_COOKIES",
            json.dumps([{"name": "reddit_session", "value": "abc123", "domain": ".reddit.com"}]),
        )
        monkeypatch.setenv("HOME", str(tmp_path))

        setup()

        cookie_file = tmp_path / ".config" / "rdt-cli" / "credential.json"
        saved = json.loads(cookie_file.read_text(encoding="utf-8"))
        assert saved == {"cookies": {"reddit_session": "abc123"}}
        assert stat.S_IMODE(cookie_file.stat().st_mode) == 0o600

    def test_preserves_full_credential_object(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv(
            "REDDIT_COOKIES",
            json.dumps({"cookies": {"reddit_session": "abc123"}, "source": "test"}),
        )
        monkeypatch.setenv("HOME", str(tmp_path))

        setup()

        saved = json.loads(
            (tmp_path / ".config" / "rdt-cli" / "credential.json").read_text(encoding="utf-8")
        )
        assert saved["source"] == "test"

    @pytest.mark.parametrize(
        "value",
        [None, "not-json", "[]", "{}", '"cookie"', '[{"name":"missing-value"}]'],
    )
    def test_rejects_missing_or_invalid_cookies(
        self, monkeypatch: pytest.MonkeyPatch, value: str | None
    ) -> None:
        if value is None:
            monkeypatch.delenv("REDDIT_COOKIES", raising=False)
        else:
            monkeypatch.setenv("REDDIT_COOKIES", value)

        with pytest.raises((RuntimeError, TypeError), match="REDDIT_COOKIES"):
            setup()


class TestPostHelpers:
    def test_builds_top_command_with_time_filter(self) -> None:
        monitor = RedditMonitor(
            name="ideas", subreddits=["SaaS"], sort="top", time="week", max_posts=50
        )

        assert build_command("SaaS", monitor) == [
            "rdt",
            "sub",
            "SaaS",
            "-s",
            "top",
            "-n",
            "50",
            "--json",
            "--compact",
            "-t",
            "week",
        ]

    def test_new_command_omits_time_filter(self) -> None:
        monitor = RedditMonitor(name="ideas", subreddits=["SaaS"], sort="new")

        assert "-t" not in build_command("SaaS", monitor)

    def test_extracts_nested_listing(self) -> None:
        raw = {
            "data": {
                "kind": "Listing",
                "data": {
                    "children": [
                        {"kind": "t3", "data": {"id": "p1", "title": "One"}},
                        {"kind": "t3", "data": {"id": "p2", "title": "Two"}},
                        "ignored",
                    ]
                },
            }
        }

        assert [post["id"] for post in extract_posts(raw)] == ["p1", "p2"]

    def test_extracts_flat_list_and_ignores_non_objects(self) -> None:
        assert extract_posts({"data": [{"id": "p1"}, None, "bad"]}) == [{"id": "p1"}]
        assert extract_posts({"unexpected": True}) == []

    def test_deduplicates_and_skips_missing_ids(self) -> None:
        posts = deduplicate_posts(
            [
                {"id": "p1", "title": "old"},
                {"title": "missing"},
                {"id": "p1", "title": "new"},
                {"id": 2, "title": "numeric"},
            ]
        )

        assert posts == [
            {"id": "p1", "title": "new"},
            {"id": "2", "title": "numeric"},
        ]


class TestComments:
    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_fetches_and_filters_comments(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                {
                    "data": [
                        {"kind": "Listing", "data": {"children": []}},
                        {
                            "kind": "Listing",
                            "data": {
                                "children": [
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "id": "c1",
                                            "author": "builder",
                                            "body": "I need this",
                                            "score": 8,
                                        },
                                    },
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "id": "c2",
                                            "author": "AutoModerator",
                                            "body": "rules",
                                            "score": 100,
                                        },
                                    },
                                    {
                                        "kind": "t1",
                                        "data": {
                                            "id": "c3",
                                            "author": "low-score",
                                            "body": "noise",
                                            "score": 0,
                                        },
                                    },
                                ]
                            },
                        },
                    ]
                }
            ),
        )

        comments = fetch_comments("p1", limit=10)

        assert comments == [
            {"id": "c1", "author": "builder", "body": "I need this", "score": 8}
        ]
        mock_run.assert_called_once_with(
            ["rdt", "read", "p1", "-n", "10", "-s", "top", "--json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_rate_limit_is_distinct_from_other_failures(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="429 Too Many Requests")
        assert fetch_comments("p1", 5) is None

        mock_run.return_value = MagicMock(returncode=1, stderr="server error")
        assert fetch_comments("p1", 5) == []

    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_timeout_and_invalid_json_return_empty(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="rdt", timeout=60)
        assert fetch_comments("p1", 5) == []

        mock_run.side_effect = None
        mock_run.return_value = MagicMock(returncode=0, stdout="not-json")
        assert fetch_comments("p1", 5) == []

    def test_selects_posts_at_percentile(self) -> None:
        candidates, threshold = select_comment_candidates(
            [
                {"id": "p0", "num_comments": 0},
                {"id": "p1", "num_comments": 5},
                {"id": "p2", "num_comments": 10},
                {"id": "p3", "num_comments": 15},
                {"id": "p4", "num_comments": 20},
            ]
        )

        assert threshold == 15
        assert [post["id"] for post in candidates] == ["p4", "p3"]

    def test_no_discussion_means_no_candidates(self) -> None:
        assert select_comment_candidates([{"id": "p1", "num_comments": 0}]) == ([], None)


class TestMergePosts:
    def test_creates_snapshot_and_parent_directories(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "nested" / "2026-08-02.json"

        total = merge_posts(snapshot, [{"id": "p1", "title": "Idea"}])

        assert total == 1
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        assert data["last_fetched"]
        assert data["posts"] == [{"id": "p1", "title": "Idea"}]
        assert not snapshot.with_name(f".{snapshot.name}.tmp").exists()

    def test_uses_collection_date_when_run_crosses_midnight(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "2026-08-01.json"

        merge_posts(snapshot, [{"id": "p1"}], fetched_on=date(2026, 8, 1))

        data = json.loads(snapshot.read_text(encoding="utf-8"))
        assert data["last_fetched"] == "2026-08-01"

    def test_updates_posts_but_preserves_existing_comments(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "last_fetched": "2026-08-01",
                    "posts": [
                        {
                            "id": "p1",
                            "title": "Old",
                            "comments_data": [{"id": "c1", "body": "useful"}],
                        },
                        {"id": "p2", "title": "Other"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        total = merge_posts(
            snapshot,
            [{"id": "p1", "title": "Updated"}, {"id": "p3", "title": "New"}],
        )

        data = json.loads(snapshot.read_text(encoding="utf-8"))
        by_id = {post["id"]: post for post in data["posts"]}
        assert total == 3
        assert by_id["p1"]["title"] == "Updated"
        assert by_id["p1"]["comments_data"] == [{"id": "c1", "body": "useful"}]

    def test_reads_legacy_bare_list(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text(json.dumps([{"id": "p1"}]), encoding="utf-8")

        assert merge_posts(snapshot, [{"id": "p2"}]) == 2

    def test_rejects_invalid_existing_snapshot(self, tmp_path: Path) -> None:
        snapshot = tmp_path / "snapshot.json"
        snapshot.write_text("not-json", encoding="utf-8")

        with pytest.raises(ValueError, match="invalid JSON"):
            merge_posts(snapshot, [{"id": "p1"}])


class TestRun:
    @patch("idea_pipeline.scraper.reddit._random_delay")
    @patch("idea_pipeline.scraper.reddit.fetch_comments")
    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_collects_deduplicates_and_enriches_discussed_posts(
        self,
        mock_run: MagicMock,
        mock_fetch_comments: MagicMock,
        _mock_delay: MagicMock,
        tmp_path: Path,
    ) -> None:
        monitor = RedditMonitor(
            name="saas",
            subreddits=["SaaS", "microsaas"],
            comments=3,
            comment_percentile=75,
        )
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "data": [
                            {"id": "p1", "num_comments": 1},
                            {"id": "p2", "num_comments": 10},
                        ]
                    }
                ),
            ),
            MagicMock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "data": [
                            {"id": "p2", "num_comments": 12},
                            {"id": "p3", "num_comments": 20},
                        ]
                    }
                ),
            ),
        ]
        mock_fetch_comments.return_value = [
            {"id": "c1", "author": "founder", "body": "pain point", "score": 5}
        ]

        assert run(monitor, tmp_path) is True

        mock_fetch_comments.assert_called_once_with("p3", limit=3)
        snapshots = list((tmp_path / "saas").glob("*.json"))
        data = json.loads(snapshots[0].read_text(encoding="utf-8"))
        by_id = {post["id"]: post for post in data["posts"]}
        assert set(by_id) == {"p1", "p2", "p3"}
        assert by_id["p2"]["num_comments"] == 12
        assert by_id["p3"]["comments_data"][0]["body"] == "pain point"

    @patch("idea_pipeline.scraper.reddit._random_delay")
    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_preserves_partial_results_after_rate_limit(
        self, mock_run: MagicMock, _mock_delay: MagicMock, tmp_path: Path
    ) -> None:
        monitor = RedditMonitor(name="saas", subreddits=["SaaS", "microsaas", "SideProject"])
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=json.dumps({"data": [{"id": "p1"}]})),
            MagicMock(returncode=1, stderr="rate limit exceeded"),
        ]

        assert run(monitor, tmp_path) is True
        assert mock_run.call_count == 2
        assert len(list((tmp_path / "saas").glob("*.json"))) == 1

    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_rate_limit_without_results_is_nonfatal(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="429")
        monitor = RedditMonitor(name="saas", subreddits=["SaaS"])

        assert run(monitor, tmp_path) is True
        assert not (tmp_path / "saas").exists()

    @patch("idea_pipeline.scraper.reddit.subprocess.run")
    def test_regular_failure_without_results_fails(self, mock_run: MagicMock, tmp_path: Path) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        monitor = RedditMonitor(name="saas", subreddits=["SaaS"])

        assert run(monitor, tmp_path) is False

    @patch("idea_pipeline.scraper.reddit.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_rdt_cli_fails_cleanly(self, _mock_run: MagicMock, tmp_path: Path) -> None:
        monitor = RedditMonitor(name="saas", subreddits=["SaaS"])

        assert run(monitor, tmp_path) is False


class TestCli:
    def test_parser_rejects_negative_comment_override(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--comments", "-1"])

    def test_empty_config_does_not_require_auth(self, tmp_path: Path) -> None:
        config = tmp_path / "reddit.yml"
        config.write_text("monitors: []\n", encoding="utf-8")

        assert main(["--config", str(config)]) == 0

    def test_unknown_monitor_returns_usage_error(self, tmp_path: Path) -> None:
        config = tmp_path / "reddit.yml"
        config.write_text("monitors:\n  - name: saas\n    subreddit: SaaS\n", encoding="utf-8")

        assert main(["--config", str(config), "--name", "missing"]) == 2

    @patch("idea_pipeline.scraper.reddit._random_delay")
    @patch("idea_pipeline.scraper.reddit.run")
    @patch("idea_pipeline.scraper.reddit.setup")
    @patch("idea_pipeline.scraper.reddit.load_dotenv")
    def test_runs_selected_monitor(
        self,
        mock_dotenv: MagicMock,
        mock_setup: MagicMock,
        mock_run: MagicMock,
        _mock_delay: MagicMock,
        tmp_path: Path,
    ) -> None:
        config = tmp_path / "reddit.yml"
        output = tmp_path / "data"
        config.write_text(
            "monitors:\n"
            "  - name: saas\n    subreddit: SaaS\n"
            "  - name: ideas\n    subreddit: AppIdeas\n",
            encoding="utf-8",
        )
        mock_run.return_value = True

        exit_code = main(
            [
                "--config",
                str(config),
                "--data-dir",
                str(output),
                "--name",
                "ideas",
                "--comments",
                "7",
            ]
        )

        assert exit_code == 0
        mock_dotenv.assert_called_once_with(DEFAULT_ENV_FILE)
        mock_setup.assert_called_once_with()
        selected = mock_run.call_args.args[0]
        assert selected.name == "ideas"
        assert selected.comments == 7
        assert mock_run.call_args == call(selected, output)
