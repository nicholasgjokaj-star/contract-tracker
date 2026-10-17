"""Save contract originals to Dropbox and generate share links.

We call Dropbox's HTTP API directly with a stored OAuth token (kept in
``DATA_DIR/dropbox.json``, which lives under the gitignored ``data/`` tree). We go
direct rather than through latchkey because latchkey's Dropbox browser-login
connector is currently broken; a token set on the app console works reliably.

The token is a short-lived (~4h) access token. If a ``refresh_token`` and
``app_key`` are also stored (from the one-time OAuth offline flow), we refresh the
access token automatically; otherwise we use the static token until it expires.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_FILE = "dropbox.json"
_CONTENT = "https://content.dropboxapi.com/2"
_API = "https://api.dropboxapi.com/2"
_OAUTH = "https://api.dropboxapi.com/oauth2/token"


class DropboxError(RuntimeError):
    """A Dropbox API call failed."""


def _config_path(data_dir: Path) -> Path:
    return data_dir / CONFIG_FILE


def load_config(data_dir: Path) -> dict[str, Any]:
    path = _config_path(data_dir)
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def save_config(data_dir: Path, config: dict[str, Any]) -> None:
    path = _config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config))
    path.chmod(0o600)


def is_configured(data_dir: Path) -> bool:
    return bool(load_config(data_dir).get("access_token"))


def status(data_dir: Path) -> dict[str, Any]:
    config = load_config(data_dir)
    return {
        "configured": bool(config.get("access_token")),
        "auto_refresh": bool(config.get("refresh_token") and config.get("app_key")),
    }


def _access_token(data_dir: Path) -> str:
    """Return a usable access token, refreshing it first if we can."""
    config = load_config(data_dir)
    token = config.get("access_token") or ""
    if not token:
        raise DropboxError("Dropbox is not connected yet.")
    refresh_token = config.get("refresh_token")
    app_key = config.get("app_key")
    expires_at = config.get("expires_at") or 0
    if refresh_token and app_key and time.time() >= expires_at - 60:
        token = _refresh(data_dir, config, refresh_token, app_key)
    return token


def _refresh(
    data_dir: Path, config: dict[str, Any], refresh_token: str, app_key: str
) -> str:
    body = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app_key,
        }
    ).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(_OAUTH, data=body), timeout=30
        ) as resp:
            payload = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise DropboxError(f"Could not refresh Dropbox token: {exc}") from exc
    token = payload["access_token"]
    config["access_token"] = token
    config["expires_at"] = time.time() + int(payload.get("expires_in", 14400))
    save_config(data_dir, config)
    return token


def folder_for_party(party: str) -> str:
    """The Dropbox folder a party's documents live in, e.g. '/Marlowe Advisory'."""
    return "/" + (_safe(party) or "Contracts")


def save_contract_file(
    data_dir: Path,
    local_path: Path,
    party: str,
    filename: str,
    folder: str | None = None,
) -> dict[str, str]:
    """Upload a contract document into Dropbox and return its path and link.

    ``folder`` pins the destination folder (used so a follow-on lands in the same
    folder as its parent agreement); if omitted, the folder is derived from the
    party name.
    """
    token = _access_token(data_dir)
    dest_folder = (folder or folder_for_party(party)).rstrip("/")
    dest = f"{dest_folder}/{_safe(filename)}"
    path_lower = _upload(token, local_path, dest)
    link = _share_link(token, path_lower)
    return {"path": path_lower, "link": link}


def set_folder_tags(data_dir: Path, path: str, tags: list[str]) -> dict[str, object]:
    """Reflect a contract's tags in its Dropbox folder as a ``_tags.txt`` marker.

    Dropbox's native file-tag API is not available to "App Folder" apps, so we
    keep a small text file listing the tags in the folder instead (visible and
    searchable in Dropbox). The marker is rewritten to match, or removed when
    there are no tags.
    """
    if not path:
        return {"synced": False}
    token = _access_token(data_dir)
    marker = f"{path.rstrip('/')}/_tags.txt"
    clean = [t.strip() for t in tags if t and t.strip()]
    if clean:
        content = ("Tags: " + ", ".join(clean) + "\n").encode()
        _upload_overwrite(token, content, marker)
    else:
        try:
            _delete(token, marker)
        except DropboxError:
            pass  # nothing to remove
    return {"synced": True, "tags": clean, "via": "marker-file"}


def _upload_overwrite(token: str, content: bytes, dest: str) -> None:
    arg = json.dumps({"path": dest, "mode": "overwrite", "mute": True})
    req = urllib.request.Request(
        f"{_CONTENT}/files/upload",
        data=content,
        headers={
            "Authorization": f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    _send(req)


def _delete(token: str, path: str) -> None:
    req = urllib.request.Request(
        f"{_API}/files/delete_v2",
        data=json.dumps({"path": path}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    _send(req)


def delete_file(data_dir: Path, path: str) -> None:
    """Delete a file from Dropbox by path. Raises DropboxError on failure."""
    if not path:
        return
    token = _access_token(data_dir)
    req = urllib.request.Request(
        f"{_API}/files/delete_v2",
        data=json.dumps({"path": path}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    _send(req)


def _upload(token: str, local_path: Path, dest: str) -> str:
    arg = json.dumps({"path": dest, "mode": "add", "autorename": True})
    req = urllib.request.Request(
        f"{_CONTENT}/files/upload",
        data=local_path.read_bytes(),
        headers={
            "Authorization": f"Bearer {token}",
            "Dropbox-API-Arg": arg,
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    payload = _send(req)
    return payload["path_lower"]


def _share_link(token: str, path_lower: str) -> str:
    body = json.dumps({"path": path_lower}).encode()
    req = urllib.request.Request(
        f"{_API}/sharing/create_shared_link_with_settings",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        return _send(req)["url"]
    except DropboxError as exc:
        # A link may already exist for this file; fetch and return it.
        if "shared_link_already_exists" in str(exc):
            return _existing_link(token, path_lower)
        raise


def _existing_link(token: str, path_lower: str) -> str:
    body = json.dumps({"path": path_lower, "direct_only": True}).encode()
    req = urllib.request.Request(
        f"{_API}/sharing/list_shared_links",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    links = _send(req).get("links", [])
    return links[0]["url"] if links else ""


def _send(req: urllib.request.Request) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise DropboxError(f"Dropbox API error ({exc.code}): {detail[:300]}") from exc
    except urllib.error.URLError as exc:
        raise DropboxError(f"Could not reach Dropbox: {exc}") from exc


def _safe(name: str) -> str:
    cleaned = "".join(c for c in (name or "") if c.isalnum() or c in "._- ").strip()
    return cleaned[:120]
