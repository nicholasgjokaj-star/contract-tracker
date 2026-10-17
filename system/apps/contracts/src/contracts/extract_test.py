"""Unit tests for the contract text extraction and JSON parsing helpers."""

from pathlib import Path

import pytest

from contracts import extract
from contracts.claude_p import ClaudeCLIError
from contracts.extract import ExtractionError, _parse_json, extract_text, parse_contract


def test_parse_json_plain_object() -> None:
    assert _parse_json('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_json_strips_json_code_fence() -> None:
    raw = '```json\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_strips_bare_code_fence() -> None:
    raw = '```\n{"a": 1}\n```'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_recovers_object_embedded_in_prose() -> None:
    raw = 'Here is the result:\n{"a": 1}\nHope that helps.'
    assert _parse_json(raw) == {"a": 1}


def test_parse_json_raises_on_non_json() -> None:
    with pytest.raises(ExtractionError):
        _parse_json("this is not json at all")


def test_parse_json_raises_on_malformed_object() -> None:
    with pytest.raises(ExtractionError):
        _parse_json('{"a": 1, "b": }')


def test_parse_contract_empty_text_raises() -> None:
    with pytest.raises(ExtractionError):
        parse_contract("   ")


def test_parse_contract_wraps_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    # A claude_p failure should surface as an ExtractionError (mapped to a 422),
    # not propagate as an uncaught error.
    def boom(prompt: str, *, system: str, model: str) -> object:
        raise ClaudeCLIError("claude -p exited 1")

    monkeypatch.setattr(extract, "claude_p_completion", boom)
    with pytest.raises(ExtractionError, match="could not analyze"):
        parse_contract("Some real contract text.")


def test_extract_text_reads_plain_text_file(tmp_path: Path) -> None:
    path = tmp_path / "contract.txt"
    path.write_text("  Agreement between the parties.  ")
    assert extract_text(path) == "Agreement between the parties."


def test_extract_text_tolerates_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "contract.txt"
    path.write_bytes(b"Agreement \xff\xfe end")
    # Should not raise; undecodable bytes are replaced.
    assert "Agreement" in extract_text(path)
