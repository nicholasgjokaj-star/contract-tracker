"""Unit tests for the SQLite storage layer."""

import sqlite3
from pathlib import Path

from contracts.db import OUR_COMPANY, ContractStore


def _store(tmp_path: Path) -> ContractStore:
    return ContractStore(tmp_path / "contracts.db")


def test_create_and_get_contract_round_trips_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract(
        {
            "contracting_party": "Acme LLC",
            "department": "Engineering",
            "est_value": "$120,000",
            "raw_extraction": {"agreement_type": "Agreement", "n": 1},
        }
    )
    contract = store.get_contract(contract_id)
    assert contract is not None
    assert contract["id"] == contract_id
    assert contract["contracting_party"] == "Acme LLC"
    assert contract["department"] == "Engineering"
    # JSON columns come back parsed, not as strings.
    assert contract["raw_extraction"] == {"agreement_type": "Agreement", "n": 1}
    # Defaults from the schema are applied.
    assert contract["status"] == "Active"
    assert contract["our_entity"] == OUR_COMPANY
    assert contract["created_at"] > 0
    assert contract["updated_at"] > 0


def test_get_missing_contract_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get_contract("con_does_not_exist") is None


def test_bool_fields_are_exposed_as_booleans(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract(
        {"contracting_party": "X", "est_value_is_guess": True, "est_value_manual": 0}
    )
    contract = store.get_contract(contract_id)
    assert contract is not None
    assert contract["est_value_is_guess"] is True
    assert contract["est_value_manual"] is False


def test_list_contracts_orders_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_contract({"contracting_party": "First"})
    second = store.create_contract({"contracting_party": "Second"})
    rows = store.list_contracts()
    assert [r["id"] for r in rows] == [second, first]


def test_update_contract_only_writes_editable_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    store.update_contract(
        contract_id,
        {
            "status": "Terminated",
            "id": "hacked",  # not editable -> ignored
            "created_at": 0,  # not editable -> ignored
            "raw_text": "nope",  # not editable -> ignored
        },
    )
    contract = store.get_contract(contract_id)
    assert contract is not None
    assert contract["status"] == "Terminated"
    assert contract["id"] == contract_id
    assert contract["created_at"] > 0
    assert contract["raw_text"] == ""


def test_update_contract_bumps_updated_at(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    before = store.get_contract(contract_id)
    assert before is not None
    store.update_contract(contract_id, {"updated_at": 0, "status": "Terminated"})
    after = store.get_contract(contract_id)
    assert after is not None
    assert after["updated_at"] >= before["updated_at"]


def test_update_contract_allows_notion_page_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    store.update_contract(contract_id, {"notion_page_id": "page123"})
    contract = store.get_contract(contract_id)
    assert contract is not None
    assert contract["notion_page_id"] == "page123"


def test_update_contract_with_no_editable_fields_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    # Should not raise even though nothing is updatable.
    store.update_contract(contract_id, {"not_a_column": "x"})
    contract = store.get_contract(contract_id)
    assert contract is not None
    assert contract["contracting_party"] == "Acme"


def test_delete_contract_removes_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    store.delete_contract(contract_id)
    assert store.get_contract(contract_id) is None


def test_documents_round_trip_and_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    first = store.add_document(
        contract_id,
        {"doc_type": "Exhibit", "name": "Exhibit A", "details": {"fee": "$10"}},
    )
    second = store.add_document(
        contract_id, {"doc_type": "Amendment", "name": "Amendment 1"}
    )
    docs = store.list_documents(contract_id)
    assert [d["id"] for d in docs] == [first, second]
    # details is a JSON column and returns parsed.
    assert docs[0]["details"] == {"fee": "$10"}


def test_update_document_only_writes_editable_fields(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    doc_id = store.add_document(contract_id, {"doc_type": "Exhibit", "name": "A"})
    store.update_document(doc_id, {"name": "Renamed", "contract_id": "other"})
    docs = store.list_documents(contract_id)
    assert docs[0]["name"] == "Renamed"
    assert docs[0]["contract_id"] == contract_id


def test_delete_document(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    doc_id = store.add_document(contract_id, {"doc_type": "Exhibit", "name": "A"})
    store.delete_document(doc_id)
    assert store.list_documents(contract_id) == []


def test_notes_round_trip_and_newest_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    store.add_note(contract_id, "Alice", "first note")
    returned = store.add_note(contract_id, "Bob", "second note")
    assert returned["author"] == "Bob"
    assert returned["body"] == "second note"
    notes = store.list_notes(contract_id)
    assert [n["body"] for n in notes] == ["second note", "first note"]


def test_contract_tags_default_empty_and_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    cid = store.create_contract({"contracting_party": "Acme"})
    assert store.get_contract(cid)["tags"] == []
    cleaned = store.set_contract_tags(cid, ["Legal", "Legal", " NDA ", ""])
    assert cleaned == ["Legal", "NDA"]  # deduped, trimmed, blanks dropped
    assert store.get_contract(cid)["tags"] == ["Legal", "NDA"]


def test_tag_vocabulary_grows_and_persists(tmp_path: Path) -> None:
    store = _store(tmp_path)
    a = store.create_contract({"contracting_party": "A"})
    b = store.create_contract({"contracting_party": "B"})
    store.set_contract_tags(a, ["Legal", "Priority"])
    store.set_contract_tags(b, ["Marketing"])
    assert store.list_tags() == ["Legal", "Marketing", "Priority"]
    # Removing a tag from every contract keeps it in the vocabulary.
    store.set_contract_tags(a, [])
    assert "Legal" in store.list_tags()
    assert "Priority" in store.list_tags()


def test_delete_note_removes_only_that_note(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    keep = store.add_note(contract_id, "Alice", "keep me")
    drop = store.add_note(contract_id, "Bob", "delete me")
    store.delete_note(drop["id"])
    remaining = store.list_notes(contract_id)
    assert [n["id"] for n in remaining] == [keep["id"]]


def test_delete_contract_cascades_to_documents_and_notes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contract_id = store.create_contract({"contracting_party": "Acme"})
    store.add_document(contract_id, {"doc_type": "Exhibit", "name": "A"})
    store.add_note(contract_id, "Alice", "note")
    store.delete_contract(contract_id)
    assert store.list_documents(contract_id) == []
    assert store.list_notes(contract_id) == []


def test_migration_adds_columns_to_an_older_database(tmp_path: Path) -> None:
    # Build a database missing the columns the migration is responsible for.
    db_path = tmp_path / "contracts.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE contracts ("
        "id TEXT PRIMARY KEY, contracting_party TEXT NOT NULL DEFAULT '', "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    conn.commit()
    conn.close()

    # Opening the store should add the missing columns without dropping data.
    store = ContractStore(db_path)
    columns = _columns(db_path, "contracts")
    assert "est_value_manual" in columns
    assert "dropbox_path" in columns
    # And the store is usable afterwards.
    contract_id = store.create_contract({"contracting_party": "Acme"})
    assert store.get_contract(contract_id) is not None


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "contracts.db"
    ContractStore(db_path)
    # Opening a second time must not raise (columns already present).
    ContractStore(db_path)
    assert "dropbox_path" in _columns(db_path, "contracts")


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()
