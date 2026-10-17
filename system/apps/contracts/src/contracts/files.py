"""Local storage for original uploaded documents.

Every uploaded contract's original file is kept under ``DATA_DIR/files/`` so the
raw source is always preserved and can be rendered natively in the app (the
preserve-and-surface principle). We would also push a copy to the user's Google
Drive folder, but latchkey does not route Google's media-upload host, so today the
in-app copy is the durable one and the Drive link is a field the user can set.
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path


def store_original(data_dir: Path, source_path: Path, original_name: str) -> str:
    """Copy an uploaded file into DATA_DIR/files and return its storage key."""
    key = uuid.uuid4().hex[:16]
    dest_dir = data_dir / "files" / key
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / _safe_name(original_name)
    shutil.copyfile(source_path, dest)
    return key


def resolve(data_dir: Path, key: str) -> Path | None:
    """Return the stored file for a key, or None if it is missing."""
    dest_dir = data_dir / "files" / key
    if not dest_dir.is_dir():
        return None
    for child in dest_dir.iterdir():
        if child.is_file():
            return child
    return None


def _safe_name(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in "._- ").strip()
    return cleaned or "document"
