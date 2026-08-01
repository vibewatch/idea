"""Refresh browser cookies by revisiting a site with Playwright."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

LogFn = Callable[[str], None]

_SAME_SITE_MAP = {
    "strict": "Strict",
    "lax": "Lax",
    "none": "None",
    "no_restriction": "None",
    "unspecified": "Lax",
}


def _parse_cookie_json(cookies_json: str) -> list[dict[str, Any]]:
    """Accept browser-export lists or rdt-cli credential dictionaries."""
    try:
        raw: Any = json.loads(cookies_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Cookie secret must contain valid JSON: {exc.msg}") from exc

    if isinstance(raw, Mapping) and "cookies" in raw:
        raw = raw["cookies"]
    if isinstance(raw, Mapping):
        raw = [{"name": name, "value": value} for name, value in raw.items()]
    if not isinstance(raw, list) or not raw:
        raise ValueError("Cookie secret must contain a non-empty cookie list")

    cookies: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or "name" not in item or "value" not in item:
            raise ValueError(f"Cookie at index {index} must contain name and value")
        name = str(item["name"]).strip()
        if not name:
            raise ValueError(f"Cookie at index {index} has an empty name")
        cookie = dict(item)
        cookie["name"] = name
        cookie["value"] = str(item["value"])
        cookies.append(cookie)
    return cookies


def _to_playwright_cookies(
    cookies: list[dict[str, Any]], default_domain: str
) -> list[dict[str, Any]]:
    """Keep only fields supported by Playwright's browser context."""
    playwright_cookies: list[dict[str, Any]] = []
    for source in cookies:
        cookie: dict[str, Any] = {
            "name": source["name"],
            "value": source["value"],
            "domain": source.get("domain") or default_domain,
            "path": source.get("path") or "/",
        }
        expires = source.get("expires")
        if isinstance(expires, (int, float)) and expires > 0:
            cookie["expires"] = expires
        if source.get("httpOnly") is True:
            cookie["httpOnly"] = True
        if source.get("secure") is True:
            cookie["secure"] = True
        raw_same_site = source.get("sameSite")
        if isinstance(raw_same_site, str):
            same_site = _SAME_SITE_MAP.get(raw_same_site.casefold())
            if same_site:
                cookie["sameSite"] = same_site
        playwright_cookies.append(cookie)
    return playwright_cookies


def refresh_cookies(
    *,
    cookies_json: str,
    url: str,
    domains: list[str],
    default_domain: str,
    screenshot_path: Path | None = None,
    wait: int = 10,
    scroll: int = 500,
    headless: bool = False,
    log_fn: LogFn = print,
) -> str:
    """Visit a site with existing cookies and return its refreshed cookie JSON."""
    source_cookies = _parse_cookie_json(cookies_json)
    playwright_cookies = _to_playwright_cookies(source_cookies, default_domain)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            context.add_cookies(playwright_cookies)
            log_fn(f"Loaded {len(playwright_cookies)} cookies")

            page = context.new_page()
            log_fn(f"Navigating to {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            log_fn(f"Page title: {page.title()}")

            if wait:
                page.wait_for_timeout(wait * 1_000)
            if scroll:
                page.evaluate("distance => window.scrollBy(0, distance)", scroll)
                page.wait_for_timeout(5_000)

            if screenshot_path is not None:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(screenshot_path), full_page=False)
                log_fn(f"Screenshot saved to {screenshot_path}")

            refreshed = context.cookies(domains)
            log_fn(f"Captured {len(refreshed)} refreshed cookies")
        finally:
            browser.close()

    if not refreshed:
        raise RuntimeError("Browser returned no cookies; refusing to overwrite the GitHub secret")
    return json.dumps(refreshed, ensure_ascii=False, separators=(",", ":"))
