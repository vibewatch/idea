"""CLI orchestration for Reddit cookie extraction and scheduled renewal."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv

from idea_pipeline import PROJECT_ROOT, setup_logging
from idea_pipeline.refresher.config import SiteConfig, load_config

DEFAULT_CONFIG = PROJECT_ROOT / "config" / "refresher" / "reddit.yml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_ARTIFACT_DIR = PROJECT_ROOT / "artifacts" / "cookie-refresh"


class RunLog:
    """Mirror safe status messages to stdout and the GitHub issue report."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, message: str) -> None:
        print(message)
        self.lines.append(message)


def _default_domain(url: str) -> str:
    host = urlparse(url).hostname
    if not host:
        raise ValueError(f"Could not determine a cookie domain from {url}")
    return f".{host}"


def process_site(
    site: SiteConfig,
    *,
    github_repo: str,
    github_token: str,
    artifact_dir: Path,
    headless: bool,
    log_fn: Callable[[str], None],
) -> bool:
    """Refresh one site and replace its configured GitHub Actions secret."""
    from idea_pipeline.refresher.browser import refresh_cookies
    from idea_pipeline.refresher.github import update_secret

    log_fn(f"[{site.name}] Starting cookie refresh")
    cookies_json = os.environ.get(site.secret_name)
    if not cookies_json:
        log_fn(f"[{site.name}] ERROR: environment variable {site.secret_name} is not set")
        return False

    screenshot_path = artifact_dir / f"{site.name}.png"
    try:
        screenshot_path.unlink(missing_ok=True)
        refreshed_json = refresh_cookies(
            cookies_json=cookies_json,
            url=site.url,
            domains=site.domains,
            default_domain=_default_domain(site.url),
            screenshot_path=screenshot_path,
            wait=site.wait,
            scroll=site.scroll,
            headless=headless,
            log_fn=lambda message: log_fn(f"[{site.name}] {message}"),
        )
        update_secret(
            github_repo,
            github_token,
            site.secret_name,
            refreshed_json,
        )
    except Exception as exc:  # noqa: BLE001 - orchestration boundary reports all site failures
        log_fn(f"[{site.name}] ERROR: {exc}")
        return False

    log_fn(f"[{site.name}] Secret '{site.secret_name}' updated")
    return True


def build_report(
    results: dict[str, bool],
    log_lines: Sequence[str],
    artifact_dir: Path,
) -> tuple[str, str]:
    """Build a GitHub issue report without including secret values."""
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    succeeded = bool(results) and all(results.values())
    status = "SUCCESS" if succeeded else "FAILED"

    rows = "\n".join(
        f"| {name} | {'OK' if ok else 'FAILED'} |" for name, ok in results.items()
    )
    screenshots = "\n".join(
        f"- `{path.name}` (workflow artifact)"
        for name in results
        if (path := artifact_dir / f"{name}.png").is_file()
    )
    if not screenshots:
        screenshots = "_No screenshots were produced._"

    title = f"Reddit Cookie Refresh [{status}] - {now}"
    body = (
        f"## Status: {status}\n\n"
        f"**Time:** {now}\n\n"
        "## Results\n\n"
        "| Site | Status |\n|---|---|\n"
        f"{rows}\n\n"
        f"## Screenshots\n\n{screenshots}\n\n"
        "## Logs\n\n```text\n"
        f"{'\n'.join(log_lines)}\n"
        "```\n"
    )
    return title, body


def _start_virtual_display(headless: bool, log_fn: Callable[[str], None]) -> Any | None:
    """Start Xvfb only for headed Linux runs without an existing display."""
    if headless or not sys.platform.startswith("linux") or os.environ.get("DISPLAY"):
        return None

    from pyvirtualdisplay import Display

    display = Display(visible=False, size=(1280, 900))
    display.start()
    log_fn("Virtual display started")
    return display


def run_refresh(*, config_path: Path, artifact_dir: Path, headless: bool) -> int:
    """Run all configured cookie refreshes and publish a GitHub issue report."""
    github_token = os.environ.get("GH_TOKEN")
    github_repo = os.environ.get("GITHUB_REPOSITORY")
    if not github_token:
        print("ERROR: GH_TOKEN environment variable is not set", file=sys.stderr)
        return 2
    if not github_repo:
        print("ERROR: GITHUB_REPOSITORY environment variable is not set", file=sys.stderr)
        return 2

    try:
        sites = load_config(config_path)
    except (FileNotFoundError, TypeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not sites:
        print(f"ERROR: no refresh sites configured in {config_path}", file=sys.stderr)
        return 2

    artifact_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog()
    log(f"Loaded {len(sites)} site(s) from {config_path}")

    results: dict[str, bool] = {}
    try:
        display = _start_virtual_display(headless, log)
    except Exception as exc:  # noqa: BLE001 - display backend errors vary by platform
        log(f"ERROR: could not start virtual display: {exc}")
        results.update({site.name: False for site in sites})
        display = None
    else:
        try:
            for site in sites:
                results[site.name] = process_site(
                    site,
                    github_repo=github_repo,
                    github_token=github_token,
                    artifact_dir=artifact_dir,
                    headless=headless,
                    log_fn=log,
                )
        finally:
            if display is not None:
                display.stop()
                log("Virtual display stopped")

    from idea_pipeline.refresher.github import create_issue

    title, body = build_report(results, log.lines, artifact_dir)
    try:
        issue_url = create_issue(
            github_repo,
            github_token,
            title,
            body,
            labels=["cookie-refresh"],
        )
        log(f"Created report: {issue_url}")
    except Exception as exc:  # noqa: BLE001 - report failure must not hide secret update result
        print(f"WARNING: failed to create refresh report: {exc}", file=sys.stderr)

    return 0 if all(results.values()) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="refresh-cookies",
        description="Refresh Reddit cookies and replace the GitHub Actions secret",
    )
    subparsers = parser.add_subparsers(dest="command")

    refresh = subparsers.add_parser("refresh", help="Refresh all configured cookie secrets")
    refresh.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    refresh.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    refresh.add_argument(
        "--headless",
        action="store_true",
        help="Use headless Chromium instead of a headed browser/Xvfb",
    )

    extract = subparsers.add_parser("extract", help="Extract initial cookies from a local browser")
    extract.add_argument("site", help="Configured site name (for this project: reddit)")
    extract.add_argument("-b", "--browser")
    extract.add_argument("-o", "--output", type=Path)
    extract.add_argument("-c", "--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the refresh CLI and return a process exit code."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    if not arguments or arguments[0].startswith("-"):
        arguments.insert(0, "refresh")

    args = build_parser().parse_args(arguments)
    setup_logging()
    load_dotenv(DEFAULT_ENV_FILE)

    if args.command == "extract":
        from idea_pipeline.refresher.extract import run_extract

        try:
            run_extract(
                site_name=args.site,
                config_path=args.config,
                browser=args.browser,
                output=args.output,
            )
        except (FileNotFoundError, RuntimeError, TypeError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        return 0

    return run_refresh(
        config_path=args.config,
        artifact_dir=args.artifact_dir,
        headless=args.headless,
    )


if __name__ == "__main__":
    raise SystemExit(main())
