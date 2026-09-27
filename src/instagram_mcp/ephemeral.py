"""Temporary copies of view-once media: deleted once their time is up."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def sweep(folder: Path, max_age_s: float, now: float | None = None) -> int:
    """Delete files under ``folder`` older than ``max_age_s``; return how many went."""
    if not folder.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_s
    removed = 0
    for path in folder.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed
