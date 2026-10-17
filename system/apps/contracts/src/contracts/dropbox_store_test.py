"""Unit tests for Dropbox config handling and path building (no network)."""

from pathlib import Path

import pytest

from contracts import dropbox_store
from contracts.dropbox_store import _safe, is_configured, save_contract_file, status


def test_safe_keeps_allowed_characters() -> None:
    assert _safe("Acme, Inc. Agreement.pdf") == "Acme Inc. Agreement.pdf"


def test_safe_truncates_to_120_chars() -> None:
    assert len(_safe("a" * 200)) == 120


def test_safe_of_empty() -> None:
    assert _safe("") == ""


def test_is_configured_false_when_no_config(tmp_path: Path) -> None:
    assert is_configured(tmp_path) is False


def test_is_configured_true_with_access_token(tmp_path: Path) -> None:
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    assert is_configured(tmp_path) is True


def test_status_reports_configured_and_auto_refresh(tmp_path: Path) -> None:
    dropbox_store.save_config(
        tmp_path, {"access_token": "tok", "refresh_token": "r", "app_key": "k"}
    )
    result = status(tmp_path)
    assert result == {"configured": True, "auto_refresh": True}


def test_status_without_refresh_credentials(tmp_path: Path) -> None:
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    assert status(tmp_path) == {"configured": True, "auto_refresh": False}


def test_save_config_is_owner_only_readable(tmp_path: Path) -> None:
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    mode = (tmp_path / dropbox_store.CONFIG_FILE).stat().st_mode & 0o777
    assert mode == 0o600


def test_save_contract_file_builds_folder_from_party(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Exercise only the pure path-building; stub the network transport.
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    local = tmp_path / "contract.pdf"
    local.write_bytes(b"pdf-bytes")

    captured: dict[str, str] = {}

    def fake_upload(token: str, local_path: Path, dest: str) -> str:
        captured["dest"] = dest
        return dest.lower()

    def fake_share(token: str, path_lower: str) -> str:
        return f"https://dropbox.example{path_lower}"

    monkeypatch.setattr(dropbox_store, "_upload", fake_upload)
    monkeypatch.setattr(dropbox_store, "_share_link", fake_share)

    result = save_contract_file(tmp_path, local, "Acme, Inc.", "My Contract.pdf")
    assert captured["dest"] == "/Acme Inc./My Contract.pdf"
    assert result["path"] == "/acme inc./my contract.pdf"
    assert result["link"].startswith("https://dropbox.example")


def test_save_contract_file_defaults_folder_when_party_blank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    local = tmp_path / "contract.pdf"
    local.write_bytes(b"pdf")
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        dropbox_store,
        "_upload",
        lambda token, local_path, dest: captured.setdefault("dest", dest) or dest,
    )
    monkeypatch.setattr(dropbox_store, "_share_link", lambda token, path: "link")
    save_contract_file(tmp_path, local, "", "doc.pdf")
    assert captured["dest"] == "/Contracts/doc.pdf"


def test_folder_for_party() -> None:
    assert dropbox_store.folder_for_party("Acme, Inc.") == "/Acme Inc."
    assert dropbox_store.folder_for_party("") == "/Contracts"


def test_save_contract_file_pins_explicit_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A follow-on saves into the parent's folder, ignoring its own party name.
    dropbox_store.save_config(tmp_path, {"access_token": "tok"})
    local = tmp_path / "amendment.pdf"
    local.write_bytes(b"pdf")
    captured: dict[str, str] = {}
    monkeypatch.setattr(
        dropbox_store,
        "_upload",
        lambda token, local_path, dest: captured.setdefault("dest", dest) or dest,
    )
    monkeypatch.setattr(dropbox_store, "_share_link", lambda token, path: "link")
    save_contract_file(
        tmp_path, local, "Some Other Party LLC", "Amendment 1.pdf",
        folder="/jane doe",
    )
    assert captured["dest"] == "/jane doe/Amendment 1.pdf"
