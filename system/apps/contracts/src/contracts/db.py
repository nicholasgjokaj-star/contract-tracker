"""SQLite storage for contracts, their documents, and notes.

All persistent state lives under ``DATA_DIR`` (see ``runner.py``). We store the
structured fields that power the search view and profile, and -- per the
preserve-the-raw-data principle -- keep the raw extracted text and the verbatim
AI extraction JSON alongside every record so a later change in how we process a
contract never requires re-fetching or re-parsing the source.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# The organization whose contracts these are ("us"). Set CONTRACTS_OUR_COMPANY to
# your own legal entity name; the placeholder is used until you do.
OUR_COMPANY = os.environ.get("CONTRACTS_OUR_COMPANY", "Your Company, Inc.")

# Departments a contract can belong to. Edit to match your organization.
DEPARTMENTS = ["Marketing", "Engineering", "Finance", "Legal", "Operations", "People"]

# Lifecycle statuses a contract can be in.
STATUSES = ["Active", "Terminated"]

# Document sections attached to a contract profile.
DOC_TYPES = ["Statement of Work", "Exhibit", "Amendment", "Addendum"]

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS contracts (
    id TEXT PRIMARY KEY,
    contracting_party TEXT NOT NULL DEFAULT '',
    our_entity TEXT NOT NULL DEFAULT '{OUR_COMPANY.replace("'", "''")}',
    execution_date TEXT,
    termination_date TEXT,
    termination_terms TEXT NOT NULL DEFAULT '',
    est_value TEXT NOT NULL DEFAULT '',
    est_value_is_guess INTEGER NOT NULL DEFAULT 0,
    est_value_manual INTEGER NOT NULL DEFAULT 0,
    est_value_basis TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    agreement_type TEXT NOT NULL DEFAULT 'Agreement',
    status TEXT NOT NULL DEFAULT 'Active',
    drive_file_id TEXT NOT NULL DEFAULT '',
    drive_link TEXT NOT NULL DEFAULT '',
    dropbox_path TEXT NOT NULL DEFAULT '',
    file_key TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    raw_extraction TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    notion_page_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tag_vocab (
    name TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    details TEXT NOT NULL DEFAULT '',
    drive_file_id TEXT NOT NULL DEFAULT '',
    drive_link TEXT NOT NULL DEFAULT '',
    file_key TEXT NOT NULL DEFAULT '',
    source_filename TEXT NOT NULL DEFAULT '',
    raw_text TEXT NOT NULL DEFAULT '',
    raw_extraction TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    contract_id TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    FOREIGN KEY (contract_id) REFERENCES contracts(id) ON DELETE CASCADE
);
"""

# Contract columns a user is allowed to edit directly from the profile.
EDITABLE_CONTRACT_FIELDS = {
    "contracting_party",
    "our_entity",
    "execution_date",
    "termination_date",
    "termination_terms",
    "est_value",
    "est_value_is_guess",
    "est_value_manual",
    "est_value_basis",
    "department",
    "owner",
    "description",
    "agreement_type",
    "status",
    "drive_link",
    "dropbox_path",
}

# Document columns a user is allowed to edit.
EDITABLE_DOC_FIELDS = {"doc_type", "name", "summary", "details", "drive_link"}

# JSON-encoded columns: stored as text, exposed to callers as parsed objects.
_JSON_CONTRACT_FIELDS = {"raw_extraction", "tags"}
_JSON_DOC_FIELDS = {"raw_extraction", "details"}

# Integer columns exposed to callers as booleans.
_BOOL_CONTRACT_FIELDS = {"est_value_is_guess", "est_value_manual"}


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class ContractStore:
    """Thin data-access layer over a single SQLite file."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the first release to older databases."""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(contracts)")}
        additions = {
            "est_value_manual": "INTEGER NOT NULL DEFAULT 0",
            "dropbox_path": "TEXT NOT NULL DEFAULT ''",
            "tags": "TEXT NOT NULL DEFAULT '[]'",
        }
        for column, decl in additions.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE contracts ADD COLUMN {column} {decl}")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tag_vocab "
            "(name TEXT PRIMARY KEY, created_at REAL NOT NULL)"
        )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- contracts -------------------------------------------------------

    def create_contract(self, fields: dict[str, Any]) -> str:
        contract_id = _new_id("con")
        row: dict[str, Any] = {
            "id": contract_id,
            "created_at": _now(),
            "updated_at": _now(),
        }
        for key, value in fields.items():
            if key in _JSON_CONTRACT_FIELDS:
                row[key] = json.dumps(value)
            elif key in _BOOL_CONTRACT_FIELDS:
                row[key] = 1 if value else 0
            else:
                row[key] = value
        columns = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO contracts ({columns}) VALUES ({placeholders})", row
            )
        return contract_id

    def update_contract(self, contract_id: str, fields: dict[str, Any]) -> None:
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in EDITABLE_CONTRACT_FIELDS and key != "notion_page_id":
                continue
            if key in _BOOL_CONTRACT_FIELDS:
                updates[key] = 1 if value else 0
            else:
                updates[key] = value
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = contract_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE contracts SET {assignments} WHERE id = :id", updates
            )

    def get_contract(self, contract_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM contracts WHERE id = ?", (contract_id,)
            ).fetchone()
        return self._contract_row(row) if row else None

    def list_contracts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM contracts ORDER BY created_at DESC"
            ).fetchall()
        return [self._contract_row(r) for r in rows]

    def delete_contract(self, contract_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))

    def _contract_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for field in _BOOL_CONTRACT_FIELDS:
            data[field] = bool(data.get(field))
        for field in _JSON_CONTRACT_FIELDS:
            data[field] = _load_json(data.get(field))
        if not isinstance(data.get("tags"), list):
            data["tags"] = []
        return data

    # ---- tags ------------------------------------------------------------

    def set_contract_tags(self, contract_id: str, tags: list[str]) -> list[str]:
        """Set a contract's tags (deduped, order-preserved) and register any new
        tag names in the growing vocabulary. Returns the cleaned tag list."""
        cleaned: list[str] = []
        for tag in tags:
            name = (tag or "").strip()
            if name and name not in cleaned:
                cleaned.append(name)
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE contracts SET tags = ?, updated_at = ? WHERE id = ?",
                (json.dumps(cleaned), now, contract_id),
            )
            for name in cleaned:
                conn.execute(
                    "INSERT OR IGNORE INTO tag_vocab (name, created_at) VALUES (?, ?)",
                    (name, now),
                )
        return cleaned

    def list_tags(self) -> list[str]:
        """All tag names ever used, sorted case-insensitively."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name FROM tag_vocab ORDER BY name COLLATE NOCASE"
            ).fetchall()
        return [r["name"] for r in rows]

    # ---- documents -------------------------------------------------------

    def add_document(self, contract_id: str, fields: dict[str, Any]) -> str:
        doc_id = _new_id("doc")
        row: dict[str, Any] = {
            "id": doc_id,
            "contract_id": contract_id,
            "created_at": _now(),
        }
        for key, value in fields.items():
            if key in _JSON_DOC_FIELDS:
                row[key] = json.dumps(value)
            else:
                row[key] = value
        columns = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row.keys())
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO documents ({columns}) VALUES ({placeholders})", row
            )
        return doc_id

    def update_document(self, doc_id: str, fields: dict[str, Any]) -> None:
        updates: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in EDITABLE_DOC_FIELDS:
                continue
            updates[key] = json.dumps(value) if key in _JSON_DOC_FIELDS else value
        if not updates:
            return
        assignments = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = doc_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE documents SET {assignments} WHERE id = :id", updates
            )

    def delete_document(self, doc_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def list_documents(self, contract_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM documents WHERE contract_id = ? ORDER BY created_at",
                (contract_id,),
            ).fetchall()
        return [self._document_row(r) for r in rows]

    def _document_row(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for field in _JSON_DOC_FIELDS:
            data[field] = _load_json(data.get(field))
        return data

    # ---- notes -----------------------------------------------------------

    def add_note(self, contract_id: str, author: str, body: str) -> dict[str, Any]:
        note_id = _new_id("note")
        created = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO notes (id, contract_id, author, body, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (note_id, contract_id, author, body, created),
            )
        return {
            "id": note_id,
            "contract_id": contract_id,
            "author": author,
            "body": body,
            "created_at": created,
        }

    def delete_note(self, note_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def list_notes(self, contract_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE contract_id = ? ORDER BY created_at DESC",
                (contract_id,),
            ).fetchall()
        return [dict(r) for r in rows]


def _load_json(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
