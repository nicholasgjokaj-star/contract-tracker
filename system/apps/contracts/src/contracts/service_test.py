"""Unit tests for upload orchestration: matching, staging, and commit."""

from datetime import date
from pathlib import Path

import pytest

from contracts import service
from contracts.db import ContractStore
from contracts.service import (
    StagingError,
    _claim_staging,
    _dates_match,
    _followon_name,
    _normalize,
    _parent_folder,
    _write_staging,
    add_attachment,
    auto_terminate_expired,
    commit_followon,
    commit_new,
    find_matches,
)

# ---- name normalization --------------------------------------------------


def test_normalize_strips_entity_suffixes_and_punctuation() -> None:
    assert _normalize("Acme, Inc.") == "acme"
    assert _normalize("Acme LLC") == "acme"
    assert _normalize("Acme Corporation") == "acme"


def test_normalize_collapses_whitespace_and_lowercases() -> None:
    assert _normalize("  ACME   Widgets  ") == "acme widgets"


def test_normalize_of_empty_is_empty() -> None:
    assert _normalize("") == ""


# ---- date matching -------------------------------------------------------


def test_dates_match_ignores_separator_and_trailing_text() -> None:
    # Same digit sequence, different separators or a trailing annotation.
    assert _dates_match("2024-01-15", "2024/01/15") is True
    assert _dates_match("2024-01-15", "2024-01-15 (Effective Date)") is True


def test_dates_match_same_year_month_when_digits_differ() -> None:
    # Falls back to a year-month prefix comparison on the raw strings.
    assert _dates_match("2024-01-15", "2024-01-31") is True


def test_dates_match_rejects_different_dates() -> None:
    assert _dates_match("2024-01-15", "2023-11-02") is False


def test_dates_match_with_empty_is_false() -> None:
    assert _dates_match("", "2024-01-15") is False


# ---- follow-on display name ----------------------------------------------


def test_followon_name_extracts_number_from_source_title() -> None:
    parsed = {"agreement_type": "Amendment", "source_title": "Amendment No. 3 to MSA"}
    assert _followon_name(parsed) == "Amendment No. 3"


def test_followon_name_without_number_uses_type() -> None:
    assert _followon_name({"agreement_type": "Addendum"}) == "Addendum"


# ---- match ranking -------------------------------------------------------


def _make_contract(store: ContractStore, **fields: object) -> str:
    base: dict[str, object] = {"contracting_party": "", "execution_date": ""}
    base.update(fields)
    return store.create_contract(base)


def test_auto_terminate_expired_flips_only_past_active_contracts(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    past = _make_contract(
        store, contracting_party="Past", termination_date="2026-01-01", status="Active"
    )
    future = _make_contract(
        store, contracting_party="Future", termination_date="2027-01-01", status="Active"
    )
    no_date = _make_contract(store, contracting_party="Open", termination_date="")
    already = _make_contract(
        store, contracting_party="Old", termination_date="2020-01-01", status="Terminated"
    )

    flipped = auto_terminate_expired(store, date(2026, 7, 23))

    assert flipped == [past]
    assert store.get_contract(past)["status"] == "Terminated"
    assert store.get_contract(future)["status"] == "Active"
    assert store.get_contract(no_date)["status"] == "Active"
    # Already-terminated stays terminated and is not re-reported.
    assert store.get_contract(already)["status"] == "Terminated"


def test_auto_terminate_on_the_date_itself_stays_active(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    cid = _make_contract(
        store, contracting_party="Today", termination_date="2026-07-23", status="Active"
    )
    assert auto_terminate_expired(store, date(2026, 7, 23)) == []
    assert store.get_contract(cid)["status"] == "Active"


def test_parent_folder_prefers_stored_path_then_party() -> None:
    assert _parent_folder({"dropbox_path": "/marlowe llc/agreement.pdf"}) == "/marlowe llc"
    assert _parent_folder({"contracting_party": "Acme, Inc."}) == "/Acme Inc."
    assert _parent_folder({}) is None


def test_add_attachment_records_document_without_dropbox(tmp_path: Path) -> None:
    # Dropbox unconfigured: the attachment is stored locally and recorded as an
    # Attachment document with no link, and never AI-parsed.
    store = ContractStore(tmp_path / "db.sqlite")
    contract_id = _make_contract(store, contracting_party="Acme, Inc.")
    src = tmp_path / "signed.pdf"
    src.write_bytes(b"a signed pdf")

    result = add_attachment(
        tmp_path, store, contract_id, src, "signed.pdf", display_name="Signed copy"
    )

    docs = store.list_documents(contract_id)
    assert len(docs) == 1
    doc = docs[0]
    assert doc["doc_type"] == "Attachment"
    assert doc["name"] == "Signed copy"
    assert doc["source_filename"] == "signed.pdf"
    assert doc["file_key"]  # the original is stored locally
    assert doc["drive_link"] == ""  # no Dropbox link when unconfigured
    assert result["dropbox"] == {"saved": False}


def test_add_attachment_unknown_contract_raises(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    src = tmp_path / "x.pdf"
    src.write_bytes(b"x")
    with pytest.raises(StagingError):
        add_attachment(tmp_path, store, "nope", src, "x.pdf")


def test_find_matches_ranks_exact_party_and_date_highest(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    exact = _make_contract(
        store, contracting_party="Acme, Inc.", execution_date="2024-01-15"
    )
    _make_contract(store, contracting_party="Globex LLC", execution_date="2020-01-01")
    parsed = {
        "referenced_agreement": {"party": "Acme Inc", "date": "2024-01-15"},
    }
    matches = find_matches(store, parsed)
    assert matches[0]["contract_id"] == exact
    # Exact party (0.7) + matching date (0.3) is capped at 1.0.
    assert matches[0]["confidence"] == 1.0
    assert "same contracting party" in matches[0]["reason"]
    assert "execution date" in matches[0]["reason"]


def test_find_matches_scores_partial_party_lower_than_exact(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    exact = _make_contract(store, contracting_party="Acme")
    partial = _make_contract(store, contracting_party="Acme Widgets Division")
    parsed = {"referenced_agreement": {"party": "Acme"}}
    matches = find_matches(store, parsed)
    ordered = [m["contract_id"] for m in matches]
    assert ordered.index(exact) < ordered.index(partial)
    partial_match = next(m for m in matches if m["contract_id"] == partial)
    assert partial_match["confidence"] == 0.5


def test_find_matches_excludes_unrelated_parties(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    _make_contract(store, contracting_party="Globex")
    parsed = {"referenced_agreement": {"party": "Acme"}}
    assert find_matches(store, parsed) == []


def test_find_matches_falls_back_to_contracting_party(tmp_path: Path) -> None:
    # When there is no referenced_agreement, the follow-on's own party is used.
    store = ContractStore(tmp_path / "db.sqlite")
    target = _make_contract(store, contracting_party="Acme Inc.")
    parsed = {"contracting_party": "Acme, Inc."}
    matches = find_matches(store, parsed)
    assert [m["contract_id"] for m in matches] == [target]


# ---- staging claim idempotency -------------------------------------------


def test_claim_staging_can_only_succeed_once(tmp_path: Path) -> None:
    analysis_id = "stg_abc123"
    _write_staging(tmp_path, analysis_id, {"analysis_id": analysis_id, "parsed": {}})
    first = _claim_staging(tmp_path, analysis_id)
    assert first["analysis_id"] == analysis_id
    # A second claim (e.g. a double-click) must fail rather than duplicate work.
    with pytest.raises(StagingError):
        _claim_staging(tmp_path, analysis_id)


def test_claim_missing_staging_raises(tmp_path: Path) -> None:
    with pytest.raises(StagingError):
        _claim_staging(tmp_path, "stg_never_written")


# ---- commit (offline: Dropbox not configured) ----------------------------


def test_commit_new_creates_contract_with_documents(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    analysis_id = "stg_new1"
    _write_staging(
        tmp_path,
        analysis_id,
        {
            "analysis_id": analysis_id,
            "owner": "Alice",
            "department": "Engineering",
            "source_filename": "msa.pdf",
            "storage_key": "",
            "raw_text": "raw contract text",
            "parsed": {
                "contracting_party": "Acme, Inc.",
                "execution_date": "2024-01-15",
                "est_value": "$120,000",
                "est_value_is_guess": False,
                "agreement_type": "Agreement",
                "description": "A services agreement.",
                "documents": [
                    {"doc_type": "Exhibit", "name": "Exhibit A", "summary": "Fees"}
                ],
            },
        },
    )
    result = commit_new(tmp_path, store, analysis_id)
    contract = store.get_contract(result["contract_id"])
    assert contract is not None
    assert contract["contracting_party"] == "Acme, Inc."
    assert contract["est_value"] == "$120,000"
    assert contract["owner"] == "Alice"
    assert contract["department"] == "Engineering"
    # The raw text and the verbatim extraction are preserved on the record.
    assert contract["raw_text"] == "raw contract text"
    assert contract["raw_extraction"]["description"] == "A services agreement."
    # Dropbox was not configured, so no upload happened.
    assert result["dropbox"] == {"saved": False}
    # The embedded exhibit was attached as a document.
    docs = store.list_documents(result["contract_id"])
    assert [d["name"] for d in docs] == ["Exhibit A"]


def test_commit_followon_attaches_document_and_applies_changes(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    parent_id = store.create_contract(
        {
            "contracting_party": "Acme, Inc.",
            "termination_date": "2024-12-31",
            "est_value": "$100,000",
        }
    )
    analysis_id = "stg_fo1"
    _write_staging(
        tmp_path,
        analysis_id,
        {
            "analysis_id": analysis_id,
            "source_filename": "amendment.pdf",
            "storage_key": "",
            "raw_text": "amendment text",
            "parsed": {
                "agreement_type": "Amendment",
                "contracting_party": "Acme, Inc.",
                "changes": "Extends the term and raises the fee.",
                "est_value": "$150,000",
                "est_value_is_guess": False,
                "termination_date": "2025-12-31",
                "source_title": "Amendment No. 2",
            },
        },
    )
    result = commit_followon(tmp_path, store, analysis_id, parent_id)
    assert result["contract_id"] == parent_id

    # The parent record picked up the amendment's new values.
    parent = store.get_contract(parent_id)
    assert parent is not None
    assert parent["est_value"] == "$150,000"
    assert parent["est_value_manual"] is False
    assert parent["termination_date"] == "2025-12-31"

    # The amendment was attached as a document with a sensible name.
    docs = store.list_documents(parent_id)
    assert len(docs) == 1
    assert docs[0]["doc_type"] == "Amendment"
    assert docs[0]["name"] == "Amendment No. 2"

    # A system note recording the change was added.
    notes = store.list_notes(parent_id)
    assert len(notes) == 1
    assert notes[0]["author"] == "System"
    assert "Extends the term" in notes[0]["body"]


def test_commit_followon_to_missing_contract_raises(tmp_path: Path) -> None:
    store = ContractStore(tmp_path / "db.sqlite")
    analysis_id = "stg_fo2"
    _write_staging(
        tmp_path,
        analysis_id,
        {"analysis_id": analysis_id, "storage_key": "", "parsed": {}},
    )
    with pytest.raises(StagingError):
        commit_followon(tmp_path, store, analysis_id, "con_missing")


def test_analyze_stages_and_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # analyze() extracts text then calls the model; stub the model so the test is
    # offline. A plain-text upload exercises the real text extraction path.
    store = ContractStore(tmp_path / "db.sqlite")
    parent_id = store.create_contract({"contracting_party": "Acme, Inc."})

    upload = tmp_path / "amendment.txt"
    upload.write_text("This is an amendment to the Acme agreement.")

    def fake_parse(text: str) -> tuple[dict, dict]:
        assert "amendment" in text.lower()
        return (
            {
                "agreement_type": "Amendment",
                "is_followon": True,
                "contracting_party": "Acme, Inc.",
                "referenced_agreement": {"party": "Acme Inc"},
            },
            {"raw_model_output": "{}", "cost_usd": 0.01, "model": "test"},
        )

    monkeypatch.setattr(service, "parse_contract", fake_parse)
    result = service.analyze(tmp_path, store, upload, "amendment.txt", "Alice", "Engineering")

    assert result["is_followon"] is True
    assert result["cost_usd"] == 0.01
    # The staged follow-on matched the existing parent contract.
    assert [m["contract_id"] for m in result["matches"]] == [parent_id]
    # A staging record was written and is claimable exactly once.
    staged = _claim_staging(tmp_path, result["analysis_id"])
    assert staged["owner"] == "Alice"
    assert staged["department"] == "Engineering"
