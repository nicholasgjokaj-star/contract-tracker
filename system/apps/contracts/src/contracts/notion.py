"""Notion sync: mirror the contract search view into a Notion database.

This module is the public surface the rest of the app calls. Config persistence
lives in ``notion_config`` and the JSON-RPC MCP transport lives in
``notion_client``; this module is the thin, safe wrapper the Flask routes use
(guarding each call so an unconfigured Notion is a no-op rather than an error).

Notion is reached through latchkey's Notion MCP endpoint. The database id (and a
contract-id -> Notion-page-id map) is persisted under ``DATA_DIR/notion.json``
once the database has been created.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from contracts import notion_client, notion_config
from contracts.db import ContractStore


def status(data_dir: Path) -> dict[str, Any]:
    config = notion_config.load_config(data_dir)
    return {
        "configured": bool(config.get("database_id")),
        "database_id": config.get("database_id", ""),
        "database_url": config.get("database_url", ""),
    }


def record_contract(
    data_dir: Path, store: ContractStore, contract_id: str
) -> dict[str, Any]:
    """Create/update the Notion row for one contract. No-op until configured."""
    config = notion_config.load_config(data_dir)
    if not config.get("database_id"):
        return {"skipped": "not_configured"}
    return notion_client.upsert_contract(data_dir, store, contract_id)


def sync_all(data_dir: Path, store: ContractStore) -> dict[str, Any]:
    """Push every contract into the Notion database."""
    config = notion_config.load_config(data_dir)
    if not config.get("database_id"):
        return {"error": "Notion database is not set up yet."}
    return notion_client.sync_all(data_dir, store)
