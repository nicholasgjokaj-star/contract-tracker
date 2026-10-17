"""Notion MCP client: create the mirror database and upsert contract rows.

Notion's hosted MCP server (``https://mcp.notion.com/mcp``) speaks JSON-RPC over
"Streamable HTTP" and replies with Server-Sent Events. We reach it through
``latchkey curl`` (credentials injected server-side). The server is stateless for
our calls, so each request is a self-contained ``latchkey curl``.

The database is created once (its data-source id and URL are saved in
``notion.json``); thereafter each contract is created or updated as a row that
mirrors the search view.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from contracts import notion_config
from contracts.db import DEPARTMENTS, ContractStore

_MCP_URL = "https://mcp.notion.com/mcp"

# Columns that mirror the search view, as a Notion SQL DDL schema.
_SCHEMA_DDL = (
    'CREATE TABLE "Contracts" ('
    '"Contracting party" TITLE, '
    f'"Department" SELECT({",".join(repr(d) for d in DEPARTMENTS)}), '
    '"Execution date" DATE, '
    '"Termination date" DATE, '
    '"Estimated value" RICH_TEXT, '
    "\"Value basis\" SELECT('Calculated','Best guess','Manually set'), "
    "\"Status\" SELECT('Active','Terminated'), "
    "\"Agreement type\" SELECT('Agreement','Amendment','Addendum','Statement of Work'), "
    '"Tags" MULTI_SELECT(), '
    '"Owner" RICH_TEXT, '
    '"Brief description" RICH_TEXT, '
    '"Drive link" URL)'
)


class NotionError(RuntimeError):
    """A Notion MCP call failed."""


def _mcp(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Send one JSON-RPC request and return the parsed ``result``."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    proc = subprocess.run(
        [
            "latchkey",
            "curl",
            "-s",
            "-X",
            "POST",
            _MCP_URL,
            "-H",
            "Content-Type: application/json",
            "-H",
            "Accept: application/json, text/event-stream",
            "-d",
            body,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise NotionError(f"latchkey curl failed: {proc.stderr.strip()}")
    payload = _parse_sse(proc.stdout)
    if "error" in payload:
        raise NotionError(str(payload["error"]))
    return payload.get("result", {})


def _parse_sse(raw: str) -> dict[str, Any]:
    """Extract the JSON-RPC object from an SSE (or plain JSON) response."""
    raw = raw.strip()
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NotionError(f"Unexpected Notion response: {raw[:200]}") from exc
    for line in raw.splitlines():
        if line.startswith("data:"):
            chunk = line[len("data:") :].strip()
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
    if "not permitted" in raw.lower():
        raise NotionError("Notion access is not authorized yet.")
    raise NotionError(f"Could not parse Notion response: {raw[:200]}")


def _call_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call an MCP tool and return its concatenated text content."""
    result = _mcp("tools/call", {"name": name, "arguments": arguments})
    if result.get("isError"):
        raise NotionError(_content_text(result) or f"{name} failed")
    return _content_text(result)


def _content_text(result: dict[str, Any]) -> str:
    parts = [
        item.get("text", "")
        for item in result.get("content", [])
        if item.get("type") == "text"
    ]
    return "\n".join(parts)


def ensure_database(data_dir: Path) -> dict[str, Any]:
    """Return the saved database config, creating the database if needed."""
    config = notion_config.load_config(data_dir)
    if config.get("database_id"):
        return config

    text = _call_tool(
        "notion-create-database",
        {
            "title": "Contracts",
            "description": "Contract register — mirrors the contract search view.",
            "schema": _SCHEMA_DDL,
        },
    )
    data_source_id = _extract(text, r"collection://([0-9a-f-]{36})")
    if not data_source_id:
        data_source_id = _extract(text, r"data[_-]source[^0-9a-f]*([0-9a-f-]{32,36})")
    url = _extract(
        text, r"(https://(?:app\.notion\.com|www\.notion\.so|notion\.so)/[^\s)\"'{}]+)"
    )
    if not data_source_id:
        raise NotionError(
            "The database was created but its id could not be read from Notion's "
            "response."
        )
    config = {
        "database_id": data_source_id,
        "data_source_id": data_source_id,
        "database_url": url or "",
    }
    notion_config.save_config(data_dir, config)
    return config


def upsert_contract(
    data_dir: Path, store: ContractStore, contract_id: str
) -> dict[str, Any]:
    """Create or update the Notion row mirroring one contract."""
    config = ensure_database(data_dir)
    contract = store.get_contract(contract_id)
    if contract is None:
        return {"error": "Contract not found"}
    _ensure_tag_options(data_dir, store, contract.get("tags") or [])
    props = _properties(contract)

    if contract.get("notion_page_id"):
        try:
            _call_tool(
                "notion-update-page",
                {
                    "page_id": contract["notion_page_id"],
                    "command": "update_properties",
                    "properties": props,
                },
            )
            _sync_documents_to_body(store, contract_id, contract["notion_page_id"])
            return {
                "ok": True,
                "page_id": contract["notion_page_id"],
                "action": "updated",
            }
        except NotionError as exc:
            # The row was deleted/archived in Notion -- fall through to recreate it.
            if "archived" not in str(exc) and "not found" not in str(exc).lower():
                raise
            store.update_contract(contract_id, {"notion_page_id": ""})

    text = _call_tool(
        "notion-create-pages",
        {
            "parent": {"data_source_id": config["data_source_id"]},
            "pages": [{"properties": props}],
        },
    )
    page_id = _extract(text, r"([0-9a-f]{32})") or _extract(text, r"([0-9a-f-]{36})")
    if page_id:
        store.update_contract(contract_id, {"notion_page_id": page_id})
        _sync_documents_to_body(store, contract_id, page_id)
    return {"ok": True, "page_id": page_id or "", "action": "created"}


# Document types that carry their own uploaded file (and thus a link) and so are
# listed with links on the contract's Notion page.
_FOLLOWON_DOC_TYPES = {"Amendment", "Addendum", "Statement of Work", "Attachment"}


def _sync_documents_to_body(
    store: ContractStore, contract_id: str, page_id: str
) -> None:
    """Write the contract's amendments/related documents (with links) into the
    Notion page body, so the primary record links out to each one.

    Best-effort: the page body is a nice-to-have, so a failure here must not break
    the property upsert.
    """
    # An empty string clears the section, so removing the last linked document
    # doesn't leave a stale list behind.
    markdown = _documents_markdown(store.list_documents(contract_id))
    try:
        _call_tool(
            "notion-update-page",
            {"page_id": page_id, "command": "replace_content", "new_str": markdown},
        )
    except NotionError:
        pass


def _ensure_tag_options(
    data_dir: Path, store: ContractStore, tags: list[str]
) -> None:
    """Register any new tag as a Tags multi-select option in the data source.

    Notion won't accept a multi_select value that isn't already a defined option,
    so before setting a page's tags we make sure the option exists. We only issue
    the (rare) schema update when a genuinely new tag appears; options track the
    growing vocabulary in ``notion.json``.
    """
    tags = [t for t in tags if t]
    if not tags:
        return
    config = notion_config.load_config(data_dir)
    known = set(config.get("tag_options", []))
    if set(tags) <= known:
        return
    vocab = [t for t in store.list_tags() if t and "," not in t]
    if not vocab:
        return
    options = ",".join("'" + t.replace("'", "''") + "'" for t in vocab)
    _call_tool(
        "notion-update-data-source",
        {
            "data_source_id": config["data_source_id"],
            "statements": f'ALTER COLUMN "Tags" SET MULTI_SELECT({options})',
        },
    )
    config["tag_options"] = vocab
    notion_config.save_config(data_dir, config)


def _documents_markdown(documents: list[dict[str, Any]]) -> str:
    """Markdown listing the follow-on documents (with links), or '' if none."""
    followons = [
        d
        for d in documents
        if d.get("doc_type") in _FOLLOWON_DOC_TYPES and d.get("drive_link")
    ]
    if not followons:
        return ""
    lines = ["## Amendments & related documents", ""]
    for doc in followons:
        name = doc.get("name") or doc.get("doc_type") or "Document"
        lines.append(f"- **{name}** — [Open in Dropbox]({doc['drive_link']})")
    return "\n".join(lines)


def sync_all(data_dir: Path, store: ContractStore) -> dict[str, Any]:
    ensure_database(data_dir)
    synced = 0
    errors: list[str] = []
    for contract in store.list_contracts():
        try:
            upsert_contract(data_dir, store, contract["id"])
            synced += 1
        except NotionError as exc:
            errors.append(f"{contract.get('contracting_party', '?')}: {exc}")
    result: dict[str, Any] = {"synced": synced}
    if errors:
        result["errors"] = errors
    config = notion_config.load_config(data_dir)
    result["database_url"] = config.get("database_url", "")
    return result


def _properties(contract: dict[str, Any]) -> dict[str, Any]:
    """Map a contract row to Notion property values."""
    props: dict[str, Any] = {
        "Contracting party": contract.get("contracting_party") or "Untitled",
        "Estimated value": contract.get("est_value") or "",
        "Value basis": _value_basis(contract),
        "Status": contract.get("status") or "Active",
        "Owner": contract.get("owner") or "",
        "Brief description": contract.get("description") or "",
        "Drive link": contract.get("drive_link") or "",
        "Tags": [t for t in (contract.get("tags") or []) if t],
    }
    if contract.get("department") in DEPARTMENTS:
        props["Department"] = contract["department"]
    agreement_type = contract.get("agreement_type") or "Agreement"
    if agreement_type in {"Agreement", "Amendment", "Addendum", "Statement of Work"}:
        props["Agreement type"] = agreement_type
    _add_date(props, "Execution date", contract.get("execution_date"))
    _add_date(props, "Termination date", contract.get("termination_date"))
    return props


def _value_basis(contract: dict[str, Any]) -> str:
    if contract.get("est_value_manual"):
        return "Manually set"
    return "Best guess" if contract.get("est_value_is_guess") else "Calculated"


def _add_date(props: dict[str, Any], name: str, value: str | None) -> None:
    iso = _iso_date(value)
    if iso:
        props[f"date:{name}:start"] = iso
        props[f"date:{name}:is_datetime"] = 0


def _iso_date(value: str | None) -> str:
    if not value:
        return ""
    match = re.search(r"\d{4}-\d{2}-\d{2}", value)
    return match.group(0) if match else ""


def _extract(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""
