"""Google Drive access through the latchkey credential proxy.

latchkey routes the standard Drive API (``https://www.googleapis.com/drive/``),
which covers reading files and folder metadata. It does NOT route Google's
separate media-upload host (``https://www.googleapis.com/upload/drive/...``), so
we cannot push raw file bytes into the user's Drive folder from here. Uploading is
therefore intentionally absent; the app keeps the original locally instead (see
``files.py``) and exposes an editable Drive link on each contract.
"""

from __future__ import annotations

import json
import os
import subprocess

# The Google Drive folder your signed agreements live in. Set
# CONTRACTS_DRIVE_FOLDER_ID to that folder's id (the last part of its URL); while it
# is unset, the Drive features are simply off.
AGREEMENTS_FOLDER_ID = os.environ.get("CONTRACTS_DRIVE_FOLDER_ID", "").strip()
AGREEMENTS_FOLDER_LINK = (
    f"https://drive.google.com/drive/folders/{AGREEMENTS_FOLDER_ID}"
    if AGREEMENTS_FOLDER_ID
    else ""
)

_DRIVE_API = "https://www.googleapis.com/drive/v3"


class DriveError(RuntimeError):
    """A Drive request through latchkey failed."""


def _curl(url: str) -> dict:
    proc = subprocess.run(
        ["latchkey", "curl", "-s", url],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise DriveError(f"latchkey curl failed: {proc.stderr.strip()}")
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise DriveError(f"Unexpected Drive response: {proc.stdout[:200]}") from exc
    if isinstance(payload, dict) and "error" in payload:
        raise DriveError(str(payload["error"]))
    return payload


def is_available() -> bool:
    """True if we can currently reach the Agreements folder (auth granted)."""
    if not AGREEMENTS_FOLDER_ID:
        return False
    try:
        _curl(
            f"{_DRIVE_API}/files/{AGREEMENTS_FOLDER_ID}"
            "?fields=id&supportsAllDrives=true"
        )
        return True
    except DriveError:
        return False


def list_agreements_folder() -> list[dict]:
    """List files currently in the Agreements folder (id, name, link)."""
    if not AGREEMENTS_FOLDER_ID:
        return []
    query = f"'{AGREEMENTS_FOLDER_ID}'+in+parents+and+trashed=false"
    url = (
        f"{_DRIVE_API}/files?q={query}"
        "&fields=files(id,name,mimeType,webViewLink,modifiedTime)"
        "&supportsAllDrives=true&includeItemsFromAllDrives=true&pageSize=100"
    )
    return _curl(url).get("files", [])
