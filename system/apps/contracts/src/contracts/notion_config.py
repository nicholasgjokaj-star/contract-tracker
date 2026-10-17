"""Persistent configuration for the Notion mirror (database id and URL).

Split out from ``notion.py`` so that both the public surface (``notion``) and the
MCP transport (``notion_client``) can read and write the config without importing
each other. Keeping this leaf module free of app dependencies makes the module
graph one-directional: ``notion`` -> ``notion_client`` -> ``notion_config``.

The config file lives at ``DATA_DIR/notion.json`` (under the gitignored ``data/``
tree) and holds the created database's id/data-source id and its URL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_FILE = "notion.json"


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
    path.write_text(json.dumps(config, indent=2))
