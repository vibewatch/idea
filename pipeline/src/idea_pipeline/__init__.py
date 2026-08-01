"""Shared paths and logging for the Python data pipeline."""

import logging
import sys
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent


def setup_logging(level: int = logging.INFO) -> None:
    """Configure consistent logging for scraper commands."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
        force=True,
    )
