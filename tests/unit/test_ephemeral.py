"""Temporary copies of view-once media are swept after their time is up."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from instagram_mcp.ephemeral import sweep

if TYPE_CHECKING:
    from pathlib import Path


def test_only_files_older_than_the_ttl_are_deleted(tmp_path: Path) -> None:
    (tmp_path / "t1").mkdir()
    old, new = tmp_path / "t1" / "old.jpg", tmp_path / "t1" / "new.jpg"
    for path, mtime in ((old, 1000), (new, 5000)):
        path.write_bytes(b"x")
        os.utime(path, (mtime, mtime))
    assert sweep(tmp_path, max_age_s=900, now=5500) == 1
    assert not old.exists()
    assert new.exists()


def test_a_missing_folder_is_a_no_op(tmp_path: Path) -> None:
    assert sweep(tmp_path / "nope", max_age_s=900) == 0
