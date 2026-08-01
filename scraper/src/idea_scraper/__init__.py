"""Shared paths and logging for the Reddit scraper application."""

import logging
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
APP_ROOT = PACKAGE_ROOT.parents[1]
REPOSITORY_ROOT = APP_ROOT.parent


def setup_logging(level: int = logging.INFO) -> None:
    """Configure consistent logging for scraper commands."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
