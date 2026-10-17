"""Unit tests for the pure Notion property-mapping helpers."""

import pytest

from contracts.notion_client import (
    NotionError,
    _documents_markdown,
    _extract,
    _iso_date,
    _parse_sse,
    _properties,
    _value_basis,
)

# ---- value basis ---------------------------------------------------------


def test_value_basis_manual_overrides_guess() -> None:
    assert (
        _value_basis({"est_value_manual": True, "est_value_is_guess": True})
        == "Manually set"
    )


def test_value_basis_guess() -> None:
    assert (
        _value_basis({"est_value_manual": False, "est_value_is_guess": True})
        == "Best guess"
    )


def test_value_basis_calculated() -> None:
    assert (
        _value_basis({"est_value_manual": False, "est_value_is_guess": False})
        == "Calculated"
    )


# ---- iso date ------------------------------------------------------------


def test_iso_date_extracts_iso_prefix() -> None:
    assert _iso_date("2024-01-15T00:00:00") == "2024-01-15"


def test_iso_date_none_and_non_iso() -> None:
    assert _iso_date(None) == ""
    assert _iso_date("sometime next year") == ""


# ---- property mapping ----------------------------------------------------


def test_properties_maps_core_fields_and_dates() -> None:
    contract = {
        "contracting_party": "Acme, Inc.",
        "department": "Engineering",
        "execution_date": "2024-01-15",
        "termination_date": "2025-01-15",
        "est_value": "$120,000",
        "est_value_manual": False,
        "est_value_is_guess": False,
        "status": "Active",
        "agreement_type": "Agreement",
        "owner": "Alice",
        "description": "Services agreement.",
        "drive_link": "https://drive.example/x",
    }
    props = _properties(contract)
    assert props["Contracting party"] == "Acme, Inc."
    assert props["Department"] == "Engineering"
    assert props["Value basis"] == "Calculated"
    assert props["Status"] == "Active"
    assert props["Agreement type"] == "Agreement"
    assert props["date:Execution date:start"] == "2024-01-15"
    assert props["date:Termination date:start"] == "2025-01-15"


def test_properties_defaults_missing_title_and_omits_unknown_selects() -> None:
    props = _properties({})
    assert props["Contracting party"] == "Untitled"
    assert props["Status"] == "Active"
    # An unknown department is not a valid select option, so it is omitted.
    assert "Department" not in props


def test_properties_omits_department_not_in_allowed_set() -> None:
    props = _properties({"department": "NotARealDept"})
    assert "Department" not in props


def test_properties_omits_dates_when_absent() -> None:
    props = _properties({"contracting_party": "Acme"})
    assert "date:Execution date:start" not in props
    assert "date:Termination date:start" not in props


# ---- regex extraction ----------------------------------------------------


def test_extract_returns_first_group_or_empty() -> None:
    assert _extract("page id is abc123", r"id is (\w+)") == "abc123"
    assert _extract("nothing here", r"id is (\w+)") == ""


# ---- SSE parsing ---------------------------------------------------------


def test_parse_sse_plain_json() -> None:
    assert _parse_sse('{"result": {"ok": true}}') == {"result": {"ok": True}}


def test_parse_sse_event_stream() -> None:
    raw = 'event: message\ndata: {"result": {"ok": true}}\n\n'
    assert _parse_sse(raw) == {"result": {"ok": True}}


def test_parse_sse_not_permitted_raises_clear_error() -> None:
    with pytest.raises(NotionError, match="not authorized"):
        _parse_sse("access is not permitted for this resource")


def test_parse_sse_unparseable_raises() -> None:
    with pytest.raises(NotionError):
        _parse_sse("garbage response with no data lines")


# ---- documents markdown (amendment links on the primary record) ----------


def test_documents_markdown_lists_followons_with_links() -> None:
    md = _documents_markdown(
        [
            {"doc_type": "Amendment", "name": "Amendment No. 1", "drive_link": "https://db/a1"},
            {"doc_type": "Statement of Work", "name": "SOW 2", "drive_link": "https://db/s2"},
            # Exhibits are sections of the original (no distinct link) -- excluded.
            {"doc_type": "Exhibit", "name": "Exhibit A", "drive_link": "https://db/orig"},
        ]
    )
    assert "## Amendments & related documents" in md
    assert "[Open in Dropbox](https://db/a1)" in md
    assert "Amendment No. 1" in md and "SOW 2" in md
    assert "Exhibit A" not in md


def test_documents_markdown_empty_without_followons() -> None:
    assert _documents_markdown([{"doc_type": "Exhibit", "drive_link": "x"}]) == ""
    assert _documents_markdown([]) == ""
