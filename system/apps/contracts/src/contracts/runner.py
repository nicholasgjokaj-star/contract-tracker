"""Contract management: upload, organize, search, and sync contracts.

A synchronous Flask app served by the threaded Werkzeug server. The app owns its
own browser origin, so it serves at ``/`` with no path prefix. Persistent state
lives under ``DATA_DIR``; the listen port is ``PORT`` (both overridable via env
vars for throwaway editing instances).
"""

import base64
import binascii
import os
import tempfile
from datetime import date
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_file
from flask.typing import ResponseReturnValue
from werkzeug.serving import run_simple

from contracts import dropbox_store, files, notion, notion_client, service
from contracts.db import DEPARTMENTS, ContractStore
from contracts.extract import ExtractionError
from contracts.service import StagingError

# Absolute, because Flask's send_file resolves a relative path against the app's
# root_path (this package's directory), not the process cwd -- so a relative DATA_DIR
# serves stored originals from the wrong place even though every other read works.
DATA_DIR = Path(os.environ.get("CONTRACTS_DATA_DIR", "data/.apps/contracts")).resolve()
PORT = int(os.environ.get("CONTRACTS_PORT", "8200"))

ASSETS = Path(__file__).parent / "assets"

app = Flask("contracts", static_folder=None)
_store: ContractStore | None = None


def store() -> ContractStore:
    global _store
    if _store is None:
        _store = ContractStore(DATA_DIR / "contracts.db")
    return _store


# ---- pages ---------------------------------------------------------------


@app.route("/")
def index() -> Response:
    return Response((ASSETS / "index.html").read_text(), mimetype="text/html")


@app.route("/app.js")
def app_js() -> Response:
    return Response((ASSETS / "app.js").read_text(), mimetype="text/javascript")


@app.route("/styles.css")
def styles() -> Response:
    return Response((ASSETS / "styles.css").read_text(), mimetype="text/css")


@app.route("/health")
def health() -> Response:
    return jsonify({"status": "ok"})


# ---- config / status -----------------------------------------------------


@app.route("/api/status")
def api_status() -> Response:
    return jsonify(
        {
            "departments": DEPARTMENTS,
            "dropbox": dropbox_store.status(DATA_DIR),
            "notion": notion.status(DATA_DIR),
        }
    )


# ---- contracts -----------------------------------------------------------


def _apply_auto_termination() -> None:
    """Flip any contract past its termination date to Terminated, syncing the
    change to Notion (best-effort). A no-op scan when nothing has expired."""
    for contract_id in service.auto_terminate_expired(store(), date.today()):
        try:
            notion.record_contract(DATA_DIR, store(), contract_id)
        except notion_client.NotionError:
            pass


@app.route("/api/contracts")
def api_contracts() -> Response:
    _apply_auto_termination()
    rows = [_contract_summary(c) for c in store().list_contracts()]
    return jsonify({"contracts": rows})


@app.route("/api/contracts/<contract_id>")
def api_contract(contract_id: str) -> ResponseReturnValue:
    _apply_auto_termination()
    contract = store().get_contract(contract_id)
    if contract is None:
        return jsonify({"error": "Contract not found"}), 404
    documents = store().list_documents(contract_id)
    notes = store().list_notes(contract_id)
    return jsonify(
        {
            "contract": contract,
            "documents": documents,
            "notes": notes,
        }
    )


@app.route("/api/contracts/<contract_id>", methods=["PATCH"])
def api_update_contract(contract_id: str) -> ResponseReturnValue:
    existing = store().get_contract(contract_id)
    if existing is None:
        return jsonify({"error": "Contract not found"}), 404
    body = dict(request.get_json(force=True))
    # A user editing the value by hand is a manual override; flag it so the UI
    # can distinguish it from an AI-calculated or AI-guessed value.
    if "est_value" in body and "est_value_manual" not in body:
        body["est_value_manual"] = True
    store().update_contract(contract_id, body)
    # Keep the Notion row current when the lifecycle status changes. Best-effort:
    # a Notion hiccup must never fail the edit the user just made.
    notion_error = ""
    if "status" in body and existing.get("notion_page_id"):
        try:
            notion.record_contract(DATA_DIR, store(), contract_id)
        except notion_client.NotionError as exc:
            notion_error = str(exc)
    return jsonify(
        {"contract": store().get_contract(contract_id), "notion_error": notion_error}
    )


@app.route("/api/contracts/<contract_id>", methods=["DELETE"])
def api_delete_contract(contract_id: str) -> Response:
    contract = store().get_contract(contract_id)
    if contract is None:
        return jsonify({"ok": True})
    dropbox_error = ""
    if contract.get("dropbox_path"):
        try:
            dropbox_store.delete_file(DATA_DIR, contract["dropbox_path"])
        except dropbox_store.DropboxError as exc:
            dropbox_error = str(exc)
    store().delete_contract(contract_id)
    return jsonify({"ok": True, "dropbox_error": dropbox_error})


@app.route("/api/documents/<doc_id>", methods=["PATCH"])
def api_update_document(doc_id: str) -> Response:
    store().update_document(doc_id, request.get_json(force=True))
    return jsonify({"ok": True})


@app.route("/api/contracts/<contract_id>/attachments", methods=["POST"])
def api_add_attachment(contract_id: str) -> ResponseReturnValue:
    if store().get_contract(contract_id) is None:
        return jsonify({"error": "Contract not found"}), 404
    payload = request.get_json(force=True)
    filename = (payload.get("filename") or "").strip()
    b64 = payload.get("content_base64") or ""
    display_name = payload.get("name") or ""
    if not filename or not b64:
        return jsonify({"error": "Please choose a file to attach."}), 400
    try:
        content = base64.b64decode(b64)
    except (ValueError, binascii.Error):
        return jsonify({"error": "The file could not be read."}), 400

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = service.add_attachment(
            DATA_DIR, store(), contract_id, tmp_path, filename, display_name
        )
    except StagingError as exc:
        return jsonify({"error": str(exc)}), 410
    finally:
        tmp_path.unlink(missing_ok=True)

    # Reflect the new attachment on the Notion record (best-effort).
    try:
        notion.record_contract(DATA_DIR, store(), contract_id)
    except notion_client.NotionError:
        pass
    return jsonify(result)


@app.route("/api/contracts/<contract_id>/notes", methods=["POST"])
def api_add_note(contract_id: str) -> ResponseReturnValue:
    if store().get_contract(contract_id) is None:
        return jsonify({"error": "Contract not found"}), 404
    payload = request.get_json(force=True)
    author = (payload.get("author") or "Anonymous").strip()
    body = (payload.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Note cannot be empty"}), 400
    note = store().add_note(contract_id, author, body)
    return jsonify({"note": note})


@app.route("/api/contracts/<contract_id>/notes/<note_id>", methods=["DELETE"])
def api_delete_note(contract_id: str, note_id: str) -> Response:
    store().delete_note(note_id)
    return jsonify({"ok": True})


# ---- tags ----------------------------------------------------------------


@app.route("/api/tags")
def api_tags() -> Response:
    return jsonify({"tags": store().list_tags()})


@app.route("/api/contracts/<contract_id>/tags", methods=["PUT"])
def api_set_tags(contract_id: str) -> ResponseReturnValue:
    contract = store().get_contract(contract_id)
    if contract is None:
        return jsonify({"error": "Contract not found"}), 404
    payload = request.get_json(force=True)
    tags = payload.get("tags")
    if not isinstance(tags, list):
        return jsonify({"error": "tags must be a list"}), 400
    cleaned = store().set_contract_tags(contract_id, [str(t) for t in tags])

    # Mirror the tags to Notion (best-effort).
    try:
        notion.record_contract(DATA_DIR, store(), contract_id)
    except notion_client.NotionError:
        pass

    # Mirror the tags onto the Dropbox folder (best-effort; needs the
    # files.metadata.write scope, otherwise reported as not synced).
    dropbox_tags: dict[str, object] = {"synced": False}
    if dropbox_store.is_configured(DATA_DIR):
        folder = service.contract_dropbox_folder(store().get_contract(contract_id))
        try:
            dropbox_tags = dropbox_store.set_folder_tags(DATA_DIR, folder or "", cleaned)
        except dropbox_store.DropboxError as exc:
            dropbox_tags = {"synced": False, "error": str(exc)}

    return jsonify({"tags": cleaned, "available": store().list_tags(), "dropbox_tags": dropbox_tags})


# ---- original files ------------------------------------------------------


@app.route("/file/<key>")
def api_file(key: str) -> ResponseReturnValue:
    path = files.resolve(DATA_DIR, key)
    if path is None:
        return jsonify({"error": "File not found"}), 404
    return send_file(path, download_name=path.name)


# ---- upload: analyze then commit ----------------------------------------


@app.route("/api/analyze", methods=["POST"])
def api_analyze() -> ResponseReturnValue:
    # The browser sends the file base64-encoded in JSON: the system_interface
    # service worker reliably passes JSON bodies but hangs on streamed multipart
    # uploads. Direct API callers may still use multipart form-data.
    if request.is_json:
        payload = request.get_json(force=True)
        filename = (payload.get("filename") or "").strip()
        b64 = payload.get("content_base64") or ""
        if not filename or not b64:
            return jsonify({"error": "Please choose a file to upload."}), 400
        try:
            content = base64.b64decode(b64)
        except (ValueError, binascii.Error):
            return jsonify({"error": "The uploaded file could not be read."}), 400
        owner = (payload.get("owner") or "").strip()
        department = (payload.get("department") or "").strip()
    else:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "Please choose a file to upload."}), 400
        filename = upload.filename
        content = upload.read()
        owner = (request.form.get("owner") or "").strip()
        department = (request.form.get("department") or "").strip()

    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        result = service.analyze(
            DATA_DIR, store(), tmp_path, filename, owner, department
        )
    except ExtractionError as exc:
        return jsonify({"error": str(exc)}), 422
    finally:
        tmp_path.unlink(missing_ok=True)
    return jsonify(result)


@app.route("/api/commit", methods=["POST"])
def api_commit() -> ResponseReturnValue:
    payload = request.get_json(force=True)
    analysis_id = payload.get("analysis_id") or ""
    decision = payload.get("decision") or "new"
    try:
        if decision == "followon":
            target = payload.get("target_contract_id") or ""
            if not target:
                return jsonify({"error": "No contract selected to update."}), 400
            result = service.commit_followon(DATA_DIR, store(), analysis_id, target)
        else:
            result = service.commit_new(DATA_DIR, store(), analysis_id)
    except StagingError as exc:
        return jsonify({"error": str(exc)}), 410

    contract_id = result["contract_id"]
    # Recording to Notion is best-effort: a Notion hiccup must not fail an upload
    # that already succeeded (the contract and its Dropbox copy are saved).
    try:
        notion_result = notion.record_contract(DATA_DIR, store(), contract_id)
    except notion_client.NotionError as exc:
        notion_result = {"error": str(exc)}
    return jsonify(
        {
            "contract_id": contract_id,
            "notion": notion_result,
            "dropbox": result.get("dropbox", {}),
        }
    )


# ---- notion --------------------------------------------------------------


@app.route("/api/notion/sync", methods=["POST"])
def api_notion_sync() -> Response:
    result = notion.sync_all(DATA_DIR, store())
    return jsonify(result)


def _contract_summary(contract: dict) -> dict:
    return {
        "id": contract["id"],
        "contracting_party": contract["contracting_party"],
        "department": contract["department"],
        "execution_date": contract["execution_date"],
        "termination_date": contract["termination_date"],
        "est_value": contract["est_value"],
        "est_value_is_guess": contract["est_value_is_guess"],
        "est_value_manual": contract["est_value_manual"],
        "est_value_basis": contract["est_value_basis"],
        "owner": contract["owner"],
        "description": contract["description"],
        "agreement_type": contract["agreement_type"],
        "status": contract["status"],
        "drive_link": contract["drive_link"],
        "tags": contract.get("tags") or [],
    }


def main() -> None:
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
