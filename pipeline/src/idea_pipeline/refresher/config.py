"""Load and validate cookie-refresh configuration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class SiteConfig(BaseModel):
    """Browser refresh settings for one site and one GitHub Actions secret."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    url: str
    secret_name: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    domains: list[str] = Field(min_length=1)
    wait: int = Field(default=10, ge=0, le=120)
    scroll: int = Field(default=500, ge=0, le=10_000)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("must be an absolute HTTP(S) URL")
        return value

    @field_validator("domains")
    @classmethod
    def _validate_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError(f"invalid cookie domain URL: {value}")
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in seen:
                normalized.append(origin)
                seen.add(origin)
        return normalized


def load_config(config_path: str | Path) -> list[SiteConfig]:
    """Load a strict YAML site list."""
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in {path}: {exc}") from exc

    if raw is None:
        return []
    if not isinstance(raw, Mapping):
        raise TypeError(f"Config root in {path} must be a mapping")

    unknown_keys = set(raw) - {"sites"}
    if unknown_keys:
        names = ", ".join(sorted(str(key) for key in unknown_keys))
        raise ValueError(f"Unknown top-level config field(s) in {path}: {names}")

    entries = raw.get("sites", [])
    if not isinstance(entries, list):
        raise TypeError(f"'sites' in {path} must be a list")

    sites: list[SiteConfig] = []
    names: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            site = SiteConfig.model_validate(entry)
        except ValidationError as exc:
            name = entry.get("name", f"index {index}") if isinstance(entry, Mapping) else index
            raise ValueError(f"Invalid config for site '{name}' in {path}:\n{exc}") from exc
        if site.name in names:
            raise ValueError(f"Duplicate site name '{site.name}' in {path}")
        names.add(site.name)
        sites.append(site)
    return sites
