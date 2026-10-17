"""Integration tests for the contracts HTTP routes (no network).

These drive the Flask app through its test client against a throwaway data dir
(see ``conftest.client``). External services (Dropbox, Notion, Claude) are never
configured under the tmp dir, so their calls no-op; the upload route stubs the
model call so the analyze/commit flow runs entirely offline.
"""

import base64

import pytest
from flask.testing import FlaskClient

from contracts import runner, service


def _create(client: FlaskClient, **parsed: object) -> str:
    """Stage and commit a new contract via the real routes, returning its id."""
    payload = {
        "contracting_party": "Acme, Inc.",
        "agreement_type": "Agreement",
        "est_value": "$100,000",
        "est_value_is_guess": False,
        "documents": [],
    }
    payload.update(parsed)

    def fake_parse(text: str) -> tuple[dict, dict]:
        return payload, {"raw_model_output": "{}", "cost_usd": 0.0, "model": "test"}

    # Analyze via the JSON path (base64), the browser's real upload path.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(service, "parse_contract", fake_parse)
        analyze = client.post(
            "/api/analyze",
            json={
                "filename": "contract.txt",
                "content_base64": base64.b64encode(b"contract text").decode(),
                "owner": "Alice",
                "department": "Engineering",
            },
        )
    assert analyze.status_code == 200
    analysis_id = analyze.get_json()["analysis_id"]
    commit = client.post(
        "/api/commit", json={"analysis_id": analysis_id, "decision": "new"}
    )
    assert commit.status_code == 200
    return commit.get_json()["contract_id"]


def test_health(client: FlaskClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_status_reports_departments_and_unconfigured_integrations(
    client: FlaskClient,
) -> None:
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["departments"] == runner.DEPARTMENTS
    assert body["dropbox"]["configured"] is False
    assert body["notion"]["configured"] is False


def test_index_and_static_assets_served(client: FlaskClient) -> None:
    assert client.get("/").status_code == 200
    assert "text/html" in client.get("/").content_type
    assert client.get("/app.js").status_code == 200
    assert client.get("/styles.css").status_code == 200


def test_contracts_list_starts_empty(client: FlaskClient) -> None:
    resp = client.get("/api/contracts")
    assert resp.status_code == 200
    assert resp.get_json() == {"contracts": []}


def test_analyze_rejects_missing_file(client: FlaskClient) -> None:
    resp = client.post("/api/analyze", json={"filename": "", "content_base64": ""})
    assert resp.status_code == 400
    assert "choose a file" in resp.get_json()["error"].lower()


def test_analyze_rejects_bad_base64(client: FlaskClient) -> None:
    resp = client.post(
        "/api/analyze", json={"filename": "c.txt", "content_base64": "!!!not-base64!!!"}
    )
    assert resp.status_code == 400


def test_upload_then_appears_in_list_and_profile(client: FlaskClient) -> None:
    contract_id = _create(client, contracting_party="Globex LLC")
    listing = client.get("/api/contracts").get_json()["contracts"]
    assert [c["contracting_party"] for c in listing] == ["Globex LLC"]

    profile = client.get(f"/api/contracts/{contract_id}")
    assert profile.status_code == 200
    body = profile.get_json()
    assert body["contract"]["id"] == contract_id
    assert body["documents"] == []
    assert body["notes"] == []


def test_get_missing_contract_is_404(client: FlaskClient) -> None:
    assert client.get("/api/contracts/con_missing").status_code == 404


def test_patch_est_value_marks_it_manual(client: FlaskClient) -> None:
    contract_id = _create(client)
    resp = client.patch(f"/api/contracts/{contract_id}", json={"est_value": "$500,000"})
    assert resp.status_code == 200
    contract = resp.get_json()["contract"]
    assert contract["est_value"] == "$500,000"
    # A hand edit of the value is flagged as a manual override.
    assert contract["est_value_manual"] is True


def test_patch_status_succeeds_without_notion(client: FlaskClient) -> None:
    contract_id = _create(client)
    resp = client.patch(f"/api/contracts/{contract_id}", json={"status": "Terminated"})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["contract"]["status"] == "Terminated"
    # Notion is not configured, so the sync is a silent no-op, not an error.
    assert body["notion_error"] == ""


def test_add_note_and_reject_empty(client: FlaskClient) -> None:
    contract_id = _create(client)
    ok = client.post(
        f"/api/contracts/{contract_id}/notes",
        json={"author": "Bob", "body": "Reviewed."},
    )
    assert ok.status_code == 200
    assert ok.get_json()["note"]["body"] == "Reviewed."

    empty = client.post(f"/api/contracts/{contract_id}/notes", json={"body": "   "})
    assert empty.status_code == 400


def test_delete_contract(client: FlaskClient) -> None:
    contract_id = _create(client)
    assert client.delete(f"/api/contracts/{contract_id}").status_code == 200
    assert client.get(f"/api/contracts/{contract_id}").status_code == 404


def test_commit_expired_analysis_is_410(client: FlaskClient) -> None:
    resp = client.post(
        "/api/commit", json={"analysis_id": "stg_never", "decision": "new"}
    )
    assert resp.status_code == 410


def test_file_route_serves_stored_original(client: FlaskClient) -> None:
    contract_id = _create(client)
    file_key = client.get(f"/api/contracts/{contract_id}").get_json()["contract"][
        "file_key"
    ]
    assert file_key
    resp = client.get(f"/file/{file_key}")
    assert resp.status_code == 200
    assert resp.data == b"contract text"


def test_file_route_missing_key_is_404(client: FlaskClient) -> None:
    assert client.get("/file/nope").status_code == 404


def test_notion_sync_without_config_reports_setup_needed(client: FlaskClient) -> None:
    resp = client.post("/api/notion/sync", json={})
    assert resp.status_code == 200
    assert "not set up" in resp.get_json()["error"].lower()
