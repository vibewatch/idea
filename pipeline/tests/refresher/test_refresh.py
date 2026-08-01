"""Tests for Reddit cookie refresh and extraction; all external I/O is mocked."""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import textwrap
from http.cookiejar import Cookie
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from nacl import encoding, public
from pydantic import ValidationError

from idea_pipeline import PROJECT_ROOT
from idea_pipeline.refresher.__main__ import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_CONFIG,
    DEFAULT_ENV_FILE,
    _default_domain,
    _start_virtual_display,
    build_report,
    main,
    process_site,
    run_refresh,
)
from idea_pipeline.refresher.browser import (
    _parse_cookie_json,
    _to_playwright_cookies,
    refresh_cookies,
)
from idea_pipeline.refresher.config import SiteConfig, load_config
from idea_pipeline.refresher.extract import (
    _domains_from_urls,
    _extract_cookies_from_jar,
    _is_matching_domain,
    extract_cookies,
    run_extract,
)
from idea_pipeline.refresher.github import (
    _encrypt_secret,
    _headers,
    create_issue,
    update_secret,
)


class TestRefreshPaths:
    def test_defaults_are_pipeline_local(self) -> None:
        assert DEFAULT_CONFIG == PROJECT_ROOT / "config" / "refresher" / "reddit.yml"
        assert DEFAULT_ENV_FILE == PROJECT_ROOT / ".env"
        assert DEFAULT_ARTIFACT_DIR == PROJECT_ROOT / "artifacts" / "cookie-refresh"


class TestSiteConfig:
    def test_defaults_and_domain_normalization(self) -> None:
        site = SiteConfig(
            name="reddit",
            url="https://www.reddit.com/",
            secret_name="REDDIT_COOKIES",
            domains=["https://reddit.com/path", "https://reddit.com/other"],
        )

        assert site.wait == 10
        assert site.scroll == 500
        assert site.domains == ["https://reddit.com"]

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("name", "../reddit"),
            ("url", "reddit.com"),
            ("secret_name", "reddit-cookies"),
            ("domains", []),
            ("domains", ["reddit.com"]),
            ("wait", -1),
            ("wait", 121),
            ("scroll", -1),
        ],
    )
    def test_rejects_invalid_values(self, field: str, value: object) -> None:
        values: dict[str, object] = {
            "name": "reddit",
            "url": "https://www.reddit.com",
            "secret_name": "REDDIT_COOKIES",
            "domains": ["https://reddit.com"],
        }
        values[field] = value

        with pytest.raises(ValidationError):
            SiteConfig.model_validate(values)


class TestRefreshConfig:
    def test_loads_valid_config(self, tmp_path: Path) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text(
            textwrap.dedent(
                """\
                sites:
                  - name: reddit
                    url: https://www.reddit.com
                    secret_name: REDDIT_COOKIES
                    domains:
                      - https://reddit.com
                    wait: 4
                    scroll: 250
                """
            ),
            encoding="utf-8",
        )

        sites = load_config(config)

        assert len(sites) == 1
        assert sites[0].name == "reddit"
        assert sites[0].wait == 4
        assert sites[0].scroll == 250

    def test_empty_document_is_empty_config(self, tmp_path: Path) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text("", encoding="utf-8")

        assert load_config(config) == []

    def test_missing_file(self) -> None:
        with pytest.raises(FileNotFoundError):
            load_config("/missing/refresh.yml")

    @pytest.mark.parametrize(
        "content",
        [
            "- not-a-mapping\n",
            "sites: not-a-list\n",
            "other: value\n",
            "sites:\n  - name: reddit\n",
            (
                "sites:\n"
                "  - name: reddit\n"
                "    url: https://reddit.com\n"
                "    secret_name: REDDIT_COOKIES\n"
                "    domains: [https://reddit.com]\n"
                "  - name: reddit\n"
                "    url: https://reddit.com\n"
                "    secret_name: REDDIT_COOKIES\n"
                "    domains: [https://reddit.com]\n"
            ),
            "sites: [\n",
        ],
    )
    def test_rejects_invalid_config(self, tmp_path: Path, content: str) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text(content, encoding="utf-8")

        with pytest.raises((TypeError, ValueError)):
            load_config(config)


class TestCookieConversion:
    def test_parses_browser_list(self) -> None:
        cookies = _parse_cookie_json('[{"name":"session","value":"abc"}]')

        assert cookies == [{"name": "session", "value": "abc"}]

    def test_parses_rdt_credential_map(self) -> None:
        cookies = _parse_cookie_json('{"cookies":{"session":"abc","token":"xyz"}}')

        assert cookies == [
            {"name": "session", "value": "abc"},
            {"name": "token", "value": "xyz"},
        ]

    @pytest.mark.parametrize(
        "value",
        ["not-json", "[]", "{}", '"cookie"', '[{"name":"missing-value"}]'],
    )
    def test_rejects_invalid_cookie_secrets(self, value: str) -> None:
        with pytest.raises(ValueError):
            _parse_cookie_json(value)

    def test_converts_supported_playwright_fields(self) -> None:
        cookies = _to_playwright_cookies(
            [
                {
                    "name": "session",
                    "value": "abc",
                    "domain": ".reddit.com",
                    "path": "/api",
                    "expires": 9_999_999_999,
                    "httpOnly": True,
                    "secure": True,
                    "sameSite": "NO_RESTRICTION",
                    "unsupported": "drop-me",
                }
            ],
            ".default.example",
        )

        assert cookies == [
            {
                "name": "session",
                "value": "abc",
                "domain": ".reddit.com",
                "path": "/api",
                "expires": 9_999_999_999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "None",
            }
        ]

    def test_adds_defaults_and_omits_false_or_expired_fields(self) -> None:
        cookies = _to_playwright_cookies(
            [
                {
                    "name": "session",
                    "value": "abc",
                    "expires": 0,
                    "httpOnly": False,
                    "secure": False,
                    "sameSite": "unknown",
                }
            ],
            ".reddit.com",
        )

        assert cookies == [
            {"name": "session", "value": "abc", "domain": ".reddit.com", "path": "/"}
        ]


class TestBrowserRefresh:
    @patch("idea_pipeline.refresher.browser.sync_playwright")
    def test_refreshes_and_captures_screenshot(
        self, mock_sync_playwright: MagicMock, tmp_path: Path
    ) -> None:
        playwright = mock_sync_playwright.return_value.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        context = browser.new_context.return_value
        page = context.new_page.return_value
        page.title.return_value = "Reddit"
        context.cookies.return_value = [
            {"name": "reddit_session", "value": "new", "domain": ".reddit.com"}
        ]
        screenshot = tmp_path / "evidence" / "reddit.png"
        logs: list[str] = []

        result = refresh_cookies(
            cookies_json='[{"name":"reddit_session","value":"old"}]',
            url="https://www.reddit.com/",
            domains=["https://reddit.com", "https://www.reddit.com"],
            default_domain=".reddit.com",
            screenshot_path=screenshot,
            wait=2,
            scroll=300,
            headless=True,
            log_fn=logs.append,
        )

        assert json.loads(result)[0]["value"] == "new"
        playwright.chromium.launch.assert_called_once_with(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context.add_cookies.assert_called_once()
        page.goto.assert_called_once_with(
            "https://www.reddit.com/", wait_until="domcontentloaded", timeout=60_000
        )
        assert page.wait_for_timeout.call_args_list[0].args == (2_000,)
        page.evaluate.assert_called_once_with("distance => window.scrollBy(0, distance)", 300)
        page.screenshot.assert_called_once_with(path=str(screenshot), full_page=False)
        context.cookies.assert_called_once_with(
            ["https://reddit.com", "https://www.reddit.com"]
        )
        browser.close.assert_called_once_with()
        assert any("Captured 1" in line for line in logs)

    @patch("idea_pipeline.refresher.browser.sync_playwright")
    def test_refuses_to_replace_secret_with_empty_result(
        self, mock_sync_playwright: MagicMock
    ) -> None:
        playwright = mock_sync_playwright.return_value.__enter__.return_value
        browser = playwright.chromium.launch.return_value
        browser.new_context.return_value.cookies.return_value = []

        with pytest.raises(RuntimeError, match="refusing to overwrite"):
            refresh_cookies(
                cookies_json='[{"name":"session","value":"old"}]',
                url="https://reddit.com",
                domains=["https://reddit.com"],
                default_domain=".reddit.com",
                wait=0,
                scroll=0,
                headless=True,
            )

        browser.close.assert_called_once_with()


class TestGitHubApi:
    def test_headers_do_not_mutate_template(self) -> None:
        assert _headers("token")["Authorization"] == "Bearer token"
        assert _headers("other")["Authorization"] == "Bearer other"

    def test_secret_encryption_round_trip(self) -> None:
        private_key = public.PrivateKey.generate()
        public_key = private_key.public_key.encode(encoding.Base64Encoder()).decode()

        encrypted = _encrypt_secret(public_key, "new-cookie-json")
        decrypted = public.SealedBox(private_key).decrypt(base64.b64decode(encrypted)).decode()

        assert decrypted == "new-cookie-json"

    @patch("idea_pipeline.refresher.github.requests")
    def test_updates_repository_secret(self, mock_requests: MagicMock) -> None:
        private_key = public.PrivateKey.generate()
        public_key = private_key.public_key.encode(encoding.Base64Encoder()).decode()
        mock_requests.get.return_value.json.return_value = {
            "key": public_key,
            "key_id": "key-123",
        }

        update_secret("owner/repo", "token", "REDDIT_COOKIES", "secret-json")

        mock_requests.get.assert_called_once()
        mock_requests.get.return_value.raise_for_status.assert_called_once_with()
        put = mock_requests.put.call_args
        assert put.args[0].endswith("/actions/secrets/REDDIT_COOKIES")
        assert put.kwargs["json"]["key_id"] == "key-123"
        mock_requests.put.return_value.raise_for_status.assert_called_once_with()

    @patch("idea_pipeline.refresher.github.requests")
    def test_issue_retries_without_unknown_label(self, mock_requests: MagicMock) -> None:
        rejected = MagicMock(status_code=422)
        accepted = MagicMock(status_code=201)
        accepted.json.return_value = {"html_url": "https://github.com/owner/repo/issues/1"}
        mock_requests.post.side_effect = [rejected, accepted]

        issue_url = create_issue(
            "owner/repo", "token", "Refresh", "Body", labels=["cookie-refresh"]
        )

        assert issue_url.endswith("/issues/1")
        assert mock_requests.post.call_count == 2
        assert "labels" not in mock_requests.post.call_args_list[1].kwargs["json"]

    @patch("idea_pipeline.refresher.github.requests")
    def test_rejects_invalid_repository_before_request(self, mock_requests: MagicMock) -> None:
        with pytest.raises(ValueError, match="owner/repository"):
            create_issue("invalid", "token", "title", "body")

        mock_requests.post.assert_not_called()


def _make_cookie(
    name: str,
    value: str,
    domain: str,
    *,
    expires: int | None = None,
    secure: bool = False,
    http_only: bool = False,
) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=secure,
        expires=expires,
        discard=False,
        comment=None,
        comment_url=None,
        rest={"HttpOnly": ""} if http_only else {},
    )


class TestLocalExtraction:
    def test_domain_urls_include_parent_for_www(self) -> None:
        domains = _domains_from_urls(["https://www.reddit.com"])

        assert domains == {"www.reddit.com", ".www.reddit.com", "reddit.com", ".reddit.com"}

    def test_domain_matching_requires_label_boundary(self) -> None:
        domains = {"reddit.com", ".reddit.com"}

        assert _is_matching_domain(".reddit.com", domains)
        assert _is_matching_domain("www.reddit.com", domains)
        assert not _is_matching_domain("evilreddit.com", domains)

    def test_converts_only_matching_cookie_jar_entries(self) -> None:
        jar = [
            _make_cookie(
                "session",
                "abc",
                ".reddit.com",
                expires=9_999_999_999,
                secure=True,
                http_only=True,
            ),
            _make_cookie("other", "xyz", ".example.com"),
        ]

        cookies = _extract_cookies_from_jar(jar, {"reddit.com", ".reddit.com"})

        assert cookies == [
            {
                "name": "session",
                "value": "abc",
                "domain": ".reddit.com",
                "path": "/",
                "expires": 9_999_999_999,
                "httpOnly": True,
                "secure": True,
            }
        ]

    def test_selects_requested_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cookie = _make_cookie("session", "abc", ".reddit.com")
        fake_module = SimpleNamespace(
            arc=MagicMock(),
            chrome=MagicMock(return_value=[cookie]),
            edge=MagicMock(),
            firefox=MagicMock(),
            brave=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "browser_cookie3", fake_module)

        cookies = extract_cookies({"reddit.com", ".reddit.com"}, browser="chrome")

        assert cookies[0]["name"] == "session"
        fake_module.chrome.assert_called_once_with()
        fake_module.arc.assert_not_called()

    def test_rejects_unknown_browser(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_module = SimpleNamespace(
            arc=MagicMock(),
            chrome=MagicMock(),
            edge=MagicMock(),
            firefox=MagicMock(),
            brave=MagicMock(),
        )
        monkeypatch.setitem(sys.modules, "browser_cookie3", fake_module)

        with pytest.raises(ValueError, match="Unknown browser"):
            extract_cookies({"reddit.com"}, browser="netscape")

    @patch("idea_pipeline.refresher.extract.extract_cookies")
    def test_extract_writes_private_cookie_file(
        self, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text(
            "sites:\n"
            "  - name: reddit\n"
            "    url: https://www.reddit.com\n"
            "    secret_name: REDDIT_COOKIES\n"
            "    domains: [https://reddit.com]\n",
            encoding="utf-8",
        )
        mock_extract.return_value = [{"name": "session", "value": "abc"}]
        output = tmp_path / "cookies.json"

        run_extract(site_name="reddit", config_path=config, output=output)

        assert json.loads(output.read_text(encoding="utf-8"))[0]["name"] == "session"
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


class TestRefreshOrchestration:
    def test_default_domain(self) -> None:
        assert _default_domain("https://www.reddit.com/path") == ".www.reddit.com"

    def test_builds_success_and_failure_reports(self, tmp_path: Path) -> None:
        (tmp_path / "reddit.png").write_bytes(b"png")

        success_title, success_body = build_report(
            {"reddit": True}, ["safe log"], tmp_path
        )
        failed_title, failed_body = build_report(
            {"reddit": False}, ["refresh failed"], tmp_path
        )

        assert "SUCCESS" in success_title
        assert "reddit.png" in success_body
        assert "safe log" in success_body
        assert "FAILED" in failed_title
        assert "FAILED" in failed_body

    @patch("idea_pipeline.refresher.github.update_secret")
    @patch("idea_pipeline.refresher.browser.refresh_cookies")
    def test_process_site_refreshes_then_updates_secret(
        self,
        mock_refresh: MagicMock,
        mock_update: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        monkeypatch.setenv("REDDIT_COOKIES", '[{"name":"session","value":"old"}]')
        mock_refresh.return_value = '[{"name":"session","value":"new"}]'
        site = SiteConfig(
            name="reddit",
            url="https://www.reddit.com",
            secret_name="REDDIT_COOKIES",
            domains=["https://reddit.com"],
        )
        logs: list[str] = []

        succeeded = process_site(
            site,
            github_repo="owner/repo",
            github_token="token",
            artifact_dir=tmp_path,
            headless=True,
            log_fn=logs.append,
        )

        assert succeeded is True
        assert mock_refresh.call_args.kwargs["screenshot_path"] == tmp_path / "reddit.png"
        mock_update.assert_called_once_with(
            "owner/repo",
            "token",
            "REDDIT_COOKIES",
            '[{"name":"session","value":"new"}]',
        )
        assert any("updated" in line for line in logs)

    @patch("idea_pipeline.refresher.browser.refresh_cookies")
    def test_process_site_skips_missing_cookie_secret(
        self, mock_refresh: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("REDDIT_COOKIES", raising=False)
        site = SiteConfig(
            name="reddit",
            url="https://reddit.com",
            secret_name="REDDIT_COOKIES",
            domains=["https://reddit.com"],
        )

        assert (
            process_site(
                site,
                github_repo="owner/repo",
                github_token="token",
                artifact_dir=tmp_path,
                headless=True,
                log_fn=lambda _message: None,
            )
            is False
        )
        mock_refresh.assert_not_called()

    @patch("idea_pipeline.refresher.__main__.sys.platform", "linux")
    @patch.dict(os.environ, {}, clear=True)
    @patch("pyvirtualdisplay.Display")
    def test_starts_virtual_display_for_headed_linux(
        self, mock_display_class: MagicMock
    ) -> None:
        display = _start_virtual_display(False, lambda _message: None)

        assert display is mock_display_class.return_value
        mock_display_class.assert_called_once_with(visible=False, size=(1280, 900))
        display.start.assert_called_once_with()

    @patch("idea_pipeline.refresher.__main__.process_site", return_value=True)
    @patch("idea_pipeline.refresher.__main__._start_virtual_display", return_value=None)
    @patch("idea_pipeline.refresher.github.create_issue")
    def test_run_refresh_reports_success(
        self,
        mock_issue: MagicMock,
        _mock_display: MagicMock,
        mock_process: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text(
            "sites:\n"
            "  - name: reddit\n"
            "    url: https://reddit.com\n"
            "    secret_name: REDDIT_COOKIES\n"
            "    domains: [https://reddit.com]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        mock_issue.return_value = "https://github.com/owner/repo/issues/1"

        exit_code = run_refresh(config_path=config, artifact_dir=tmp_path / "artifacts", headless=True)

        assert exit_code == 0
        mock_process.assert_called_once()
        mock_issue.assert_called_once()

    @patch("idea_pipeline.refresher.__main__._start_virtual_display")
    @patch("idea_pipeline.refresher.github.create_issue")
    def test_run_refresh_reports_virtual_display_failure(
        self,
        mock_issue: MagicMock,
        mock_display: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        config = tmp_path / "refresh.yml"
        config.write_text(
            "sites:\n"
            "  - name: reddit\n"
            "    url: https://reddit.com\n"
            "    secret_name: REDDIT_COOKIES\n"
            "    domains: [https://reddit.com]\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("GH_TOKEN", "token")
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        mock_display.side_effect = RuntimeError("Xvfb unavailable")
        mock_issue.return_value = "https://github.com/owner/repo/issues/1"

        exit_code = run_refresh(config_path=config, artifact_dir=tmp_path, headless=False)

        assert exit_code == 1
        assert "FAILED" in mock_issue.call_args.args[2]
        assert "Xvfb unavailable" in mock_issue.call_args.args[3]

    def test_run_refresh_requires_github_environment(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.delenv("GH_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

        assert run_refresh(config_path=tmp_path / "missing.yml", artifact_dir=tmp_path, headless=True) == 2


class TestRefreshCli:
    @patch("idea_pipeline.refresher.__main__.run_refresh", return_value=0)
    @patch("idea_pipeline.refresher.__main__.load_dotenv")
    def test_no_subcommand_defaults_to_refresh(
        self, mock_dotenv: MagicMock, mock_run_refresh: MagicMock
    ) -> None:
        assert main([]) == 0

        mock_dotenv.assert_called_once_with(DEFAULT_ENV_FILE)
        assert mock_run_refresh.call_args.kwargs == {
            "config_path": DEFAULT_CONFIG,
            "artifact_dir": DEFAULT_ARTIFACT_DIR,
            "headless": False,
        }

    @patch("idea_pipeline.refresher.extract.run_extract")
    @patch("idea_pipeline.refresher.__main__.load_dotenv")
    def test_extract_subcommand(
        self, _mock_dotenv: MagicMock, mock_extract: MagicMock, tmp_path: Path
    ) -> None:
        output = tmp_path / "cookies.json"

        assert main(["extract", "reddit", "--browser", "chrome", "--output", str(output)]) == 0

        assert mock_extract.call_args.kwargs == {
            "site_name": "reddit",
            "config_path": DEFAULT_CONFIG,
            "browser": "chrome",
            "output": output,
        }
