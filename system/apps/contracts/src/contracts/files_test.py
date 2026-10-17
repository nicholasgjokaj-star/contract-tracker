"""Unit tests for local original-file storage."""

from pathlib import Path

from contracts.files import _safe_name, resolve, store_original


def test_store_and_resolve_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"the original bytes")
    key = store_original(tmp_path, source, "My Contract.pdf")
    resolved = resolve(tmp_path, key)
    assert resolved is not None
    assert resolved.read_bytes() == b"the original bytes"
    assert resolved.name == "My Contract.pdf"


def test_resolve_missing_key_returns_none(tmp_path: Path) -> None:
    assert resolve(tmp_path, "nonexistent") is None


def test_store_original_sanitizes_name(tmp_path: Path) -> None:
    source = tmp_path / "upload.pdf"
    source.write_bytes(b"x")
    key = store_original(tmp_path, source, "weird/../name*.pdf")
    resolved = resolve(tmp_path, key)
    assert resolved is not None
    assert "/" not in resolved.name
    assert "*" not in resolved.name


def test_safe_name_falls_back_to_document() -> None:
    assert _safe_name("///") == "document"
    assert _safe_name("Statement of Work 1.pdf") == "Statement of Work 1.pdf"
