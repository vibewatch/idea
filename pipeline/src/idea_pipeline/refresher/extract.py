"""Extract Reddit cookies from a local browser in Playwright-compatible form."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from idea_pipeline.refresher.config import load_config

_SUPPORTED_BROWSERS = ("arc", "chrome", "edge", "firefox", "brave")


def _domains_from_urls(urls: list[str]) -> set[str]:
    """Convert origin URLs into exact and parent-domain cookie matches."""
    domains: set[str] = set()
    for url in urls:
        host = urlparse(url).hostname or url
        domains.update({host, f".{host}"})
        if host.startswith("www."):
            parent = host.removeprefix("www.")
            domains.update({parent, f".{parent}"})
    return domains


def _is_matching_domain(cookie_domain: str, match_domains: set[str]) -> bool:
    candidate = cookie_domain.casefold().removeprefix(".")
    for domain in match_domains:
        target = domain.casefold().removeprefix(".")
        if candidate == target or candidate.endswith(f".{target}"):
            return True
    return False


def _extract_cookies_from_jar(
    jar: Iterable[Any], match_domains: set[str]
) -> list[dict[str, Any]]:
    """Convert matching ``http.cookiejar`` entries to Playwright dictionaries."""
    cookies: list[dict[str, Any]] = []
    for cookie in jar:
        domain = cookie.domain or ""
        if not _is_matching_domain(domain, match_domains):
            continue

        entry: dict[str, Any] = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": domain,
            "path": cookie.path or "/",
        }
        if cookie.expires and cookie.expires > 0:
            entry["expires"] = cookie.expires
        if cookie.get_nonstandard_attr("HttpOnly") is not None:
            entry["httpOnly"] = True
        if cookie.secure:
            entry["secure"] = True
        cookies.append(entry)
    return cookies


def extract_cookies(match_domains: set[str], browser: str | None = None) -> list[dict[str, Any]]:
    """Read matching cookies from one browser, or try supported browsers in order."""
    try:
        import browser_cookie3
    except ImportError as exc:
        raise RuntimeError(
            "Local extraction requires the optional dependency. "
            "Run: uv sync --project pipeline --extra extract"
        ) from exc

    browser_functions = {
        "arc": browser_cookie3.arc,
        "chrome": browser_cookie3.chrome,
        "edge": browser_cookie3.edge,
        "firefox": browser_cookie3.firefox,
        "brave": browser_cookie3.brave,
    }
    if browser is not None:
        selected = browser.casefold()
        if selected not in browser_functions:
            choices = ", ".join(_SUPPORTED_BROWSERS)
            raise ValueError(f"Unknown browser '{browser}'; choose from: {choices}")
        order = [selected]
    else:
        order = list(_SUPPORTED_BROWSERS)

    attempts: list[str] = []
    for name in order:
        try:
            cookies = _extract_cookies_from_jar(browser_functions[name](), match_domains)
        except Exception as exc:  # noqa: BLE001 - browser backends raise platform-specific errors
            attempts.append(f"{name}: {exc}")
            continue
        if cookies:
            return cookies
        attempts.append(f"{name}: no matching cookies")

    domains = ", ".join(sorted(domain for domain in match_domains if not domain.startswith(".")))
    details = "\n".join(f"  - {attempt}" for attempt in attempts)
    raise RuntimeError(
        f"No cookies found for {domains}. Confirm that Reddit is logged in.\nAttempted:\n{details}"
    )


def run_extract(
    *,
    site_name: str,
    config_path: Path,
    browser: str | None = None,
    output: Path | None = None,
) -> None:
    """Extract one configured site's cookies and write or print JSON."""
    sites = load_config(config_path)
    site = next((candidate for candidate in sites if candidate.name == site_name), None)
    if site is None:
        available = ", ".join(candidate.name for candidate in sites) or "none"
        raise ValueError(f"Site '{site_name}' not found; available sites: {available}")

    match_domains = _domains_from_urls(site.domains)
    cookies = extract_cookies(match_domains, browser=browser)
    cookies_json = json.dumps(cookies, indent=2, ensure_ascii=False) + "\n"

    if output is None:
        print(cookies_json, end="")
        print(
            "Save this JSON to a file, then run "
            f"'gh secret set {site.secret_name} < cookies.json'.",
            file=sys.stderr,
        )
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(cookies_json, encoding="utf-8")
    output.chmod(0o600)
    print(f"Saved {len(cookies)} cookies to {output}", file=sys.stderr)
    print(f"Run: gh secret set {site.secret_name} < {output}", file=sys.stderr)
