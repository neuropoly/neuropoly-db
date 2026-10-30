"""Dataset-boundary checks shared by validation and repair."""

import os
from pathlib import Path

from bids_repair.errors import BidsRepairError


def assert_safe_path(root: Path, path: Path, *, must_exist: bool) -> None:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(root) or not absolute.resolve().is_relative_to(root):
        raise BidsRepairError(f"Repair path escapes the dataset root: {path}")

    current = root
    if root.is_symlink():
        raise BidsRepairError(f"Dataset root is a symlink: {root}")
    for part in absolute.relative_to(root).parts:
        current = current / part
        if current.is_symlink():
            raise BidsRepairError(f"Repair path contains a symlink: {current}")

    if must_exist and not absolute.exists():
        raise BidsRepairError(f"Repair source no longer exists: {absolute}")
