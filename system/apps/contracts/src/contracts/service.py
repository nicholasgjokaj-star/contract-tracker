"""Upload orchestration: analyze a document, match follow-ons, then commit.

The upload happens in two phases so the user can confirm a match before anything
is written:

1. ``analyze`` stores the original file, extracts and parses it, finds candidate
   original contracts for a follow-on (amendment / addendum / additional SOW), and
   saves a staging record. Nothing is added to the contract list yet.
2. ``commit_new`` / ``commit_followon`` act on the user's decision -- either create
   a new contract, or attach the follow-on to the chosen original and apply its
   changes.
"""

from __future__ import annotations

import json
import posixpath
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from contracts import drive, dropbox_store, files
from contracts.db import OUR_COMPANY, ContractStore
from contracts.dropbox_store import DropboxError
from contracts.extract import extract_text, parse_contract

_FOLLOWON_TYPES = {"Amendment", "Addendum", "Statement of Work"}


class StagingError(RuntimeError):
    """A staging record could not be found or read."""


def analyze(
    data_dir: Path,
    store: ContractStore,
    upload_path: Path,
    original_name: str,
    owner: str,
    department: str,
) -> dict[str, Any]:
    """Phase 1: store, parse, and match. Returns the staged analysis."""
    storage_key = files.store_original(data_dir, upload_path, original_name)
    stored = files.resolve(data_dir, storage_key)
    text = extract_text(stored) if stored else ""
    parsed, meta = parse_contract(text)

    is_followon = bool(parsed.get("is_followon")) or (
        parsed.get("agreement_type") in _FOLLOWON_TYPES
    )
    matches = find_matches(store, parsed) if is_followon else []

    analysis_id = f"stg_{uuid.uuid4().hex[:12]}"
    staging = {
        "analysis_id": analysis_id,
        "owner": owner,
        "department": department,
        "source_filename": original_name,
        "storage_key": storage_key,
        "raw_text": text,
        "parsed": parsed,
        "meta": meta,
        "is_followon": is_followon,
        "matches": matches,
    }
    _write_staging(data_dir, analysis_id, staging)

    return {
        "analysis_id": analysis_id,
        "parsed": parsed,
        "is_followon": is_followon,
        "matches": matches,
        "source_filename": original_name,
        "cost_usd": meta.get("cost_usd"),
    }


def commit_new(
    data_dir: Path, store: ContractStore, analysis_id: str
) -> dict[str, Any]:
    """Phase 2a: create a brand-new contract from a staged analysis."""
    staging = _claim_staging(data_dir, analysis_id)
    parsed = staging["parsed"]
    party = parsed.get("contracting_party") or "Contract"
    dbx = _save_to_dropbox(data_dir, staging, party)
    doc_link = dbx.get("link") or drive.AGREEMENTS_FOLDER_LINK

    contract_id = store.create_contract(
        {
            "contracting_party": parsed.get("contracting_party") or "",
            "our_entity": parsed.get("our_entity") or OUR_COMPANY,
            "execution_date": parsed.get("execution_date") or "",
            "termination_date": parsed.get("termination_date") or "",
            "termination_terms": parsed.get("termination_terms") or "",
            "est_value": parsed.get("est_value") or "",
            "est_value_is_guess": bool(parsed.get("est_value_is_guess")),
            "est_value_basis": parsed.get("est_value_basis") or "",
            "department": staging.get("department") or "",
            "owner": staging.get("owner") or "",
            "description": parsed.get("description") or "",
            "agreement_type": parsed.get("agreement_type") or "Agreement",
            "drive_link": doc_link,
            "dropbox_path": dbx.get("path", ""),
            "file_key": staging.get("storage_key") or "",
            "source_filename": staging.get("source_filename") or "",
            "raw_text": staging.get("raw_text") or "",
            "raw_extraction": parsed,
        }
    )
    # The original itself is reachable from the contract header ("View original"
    # and the Dropbox link), so it is deliberately not added as a document here --
    # the documents section is only for statements of work, exhibits, amendments,
    # and addenda.
    # Attach any exhibits / schedules the parser found inside it.
    for doc in parsed.get("documents") or []:
        store.add_document(
            contract_id,
            {
                "doc_type": doc.get("doc_type") or "Exhibit",
                "name": doc.get("name") or "Document",
                "summary": doc.get("summary") or "",
                "details": doc.get("details") or {},
                "drive_link": doc_link,
            },
        )
    return {"contract_id": contract_id, "dropbox": dbx}


def commit_followon(
    data_dir: Path, store: ContractStore, analysis_id: str, target_contract_id: str
) -> dict[str, Any]:
    """Phase 2b: attach a follow-on to an existing contract and apply changes."""
    staging = _claim_staging(data_dir, analysis_id)
    parsed = staging["parsed"]
    target = store.get_contract(target_contract_id)
    if target is None:
        raise StagingError("The selected contract no longer exists.")

    party = parsed.get("contracting_party") or target.get("contracting_party") or ""
    # Save the follow-on into the SAME Dropbox folder as the parent agreement.
    dbx = _save_to_dropbox(data_dir, staging, party, folder=_parent_folder(target))
    doc_link = dbx.get("link") or drive.AGREEMENTS_FOLDER_LINK

    doc_type = parsed.get("agreement_type") or "Amendment"
    doc_id = store.add_document(
        target_contract_id,
        {
            "doc_type": doc_type,
            "name": _followon_name(parsed),
            "summary": parsed.get("changes") or parsed.get("description") or "",
            "details": {
                "changes": parsed.get("changes") or "",
                "documents": parsed.get("documents") or [],
                "full_extraction": parsed,
            },
            "drive_link": doc_link,
            "file_key": staging.get("storage_key") or "",
            "source_filename": staging.get("source_filename") or "",
            "raw_text": staging.get("raw_text") or "",
            "raw_extraction": parsed,
        },
    )

    # Apply the follow-on's changes to the parent record where it states new values.
    updates: dict[str, Any] = {}
    if parsed.get("est_value"):
        updates["est_value"] = parsed["est_value"]
        updates["est_value_is_guess"] = bool(parsed.get("est_value_is_guess"))
        updates["est_value_manual"] = False
        updates["est_value_basis"] = parsed.get("est_value_basis") or ""
    if parsed.get("termination_date"):
        updates["termination_date"] = parsed["termination_date"]
    if updates:
        store.update_contract(target_contract_id, updates)

    # Record what changed as an automatic, attributed note.
    change_summary = parsed.get("changes") or "Follow-on document added."
    store.add_note(
        target_contract_id,
        author="System",
        body=f"{doc_type} added: {change_summary}",
    )
    return {
        "contract_id": target_contract_id,
        "document_id": doc_id,
        "dropbox": dbx,
    }


def add_attachment(
    data_dir: Path,
    store: ContractStore,
    contract_id: str,
    upload_path: Path,
    filename: str,
    display_name: str = "",
) -> dict[str, Any]:
    """Attach an arbitrary file to a contract: store it, save it into the
    contract's Dropbox folder, and record it as an Attachment document. No AI
    parsing -- an attachment is just a file the user wants kept with the contract.
    """
    contract = store.get_contract(contract_id)
    if contract is None:
        raise StagingError("The contract no longer exists.")
    storage_key = files.store_original(data_dir, upload_path, filename)
    stored = files.resolve(data_dir, storage_key)

    dbx: dict[str, Any] = {"saved": False}
    link = ""
    if dropbox_store.is_configured(data_dir) and stored is not None:
        try:
            res = dropbox_store.save_contract_file(
                data_dir,
                stored,
                contract.get("contracting_party") or "",
                filename,
                folder=_parent_folder(contract),
            )
            link = res["link"]
            dbx = {"saved": True, "link": link, "path": res["path"]}
        except DropboxError as exc:
            dbx = {"saved": False, "error": str(exc)}

    doc_id = store.add_document(
        contract_id,
        {
            "doc_type": "Attachment",
            "name": (display_name or "").strip() or filename,
            "summary": "",
            "details": {},
            "drive_link": link,
            "file_key": storage_key,
            "source_filename": filename,
        },
    )
    return {"document_id": doc_id, "dropbox": dbx}


def contract_dropbox_folder(contract: dict[str, Any]) -> str | None:
    """Public accessor for the Dropbox folder a contract's files live in."""
    return _parent_folder(contract)


def _parent_folder(contract: dict[str, Any]) -> str | None:
    """The Dropbox folder a contract's files live in."""
    if contract.get("dropbox_path"):
        return posixpath.dirname(contract["dropbox_path"])
    if contract.get("contracting_party"):
        return dropbox_store.folder_for_party(contract["contracting_party"])
    return None


def auto_terminate_expired(store: ContractStore, today: date) -> list[str]:
    """Mark any active contract whose termination date has passed as Terminated.

    Returns the ids that were flipped, so the caller can sync them to Notion.
    Only touches contracts that are not already Terminated, and only when a real
    date has genuinely passed -- a blank/unparseable date is left alone.
    """
    flipped: list[str] = []
    for contract in store.list_contracts():
        if contract.get("status") == "Terminated":
            continue
        term = _parse_iso_date(contract.get("termination_date"))
        if term is not None and term < today:
            store.update_contract(contract["id"], {"status": "Terminated"})
            flipped.append(contract["id"])
    return flipped


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not match:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def find_matches(store: ContractStore, parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Rank existing contracts as candidate originals for a follow-on."""
    ref = parsed.get("referenced_agreement") or {}
    ref_party = _normalize(ref.get("party") or parsed.get("contracting_party") or "")
    ref_date = ref.get("date") or ""

    scored: list[tuple[float, dict[str, Any]]] = []
    for contract in store.list_contracts():
        cand_party = _normalize(contract.get("contracting_party") or "")
        if not cand_party or not ref_party:
            continue
        score = 0.0
        reasons = []
        if cand_party == ref_party:
            score += 0.7
            reasons.append("same contracting party")
        elif ref_party in cand_party or cand_party in ref_party:
            score += 0.5
            reasons.append("similar contracting party")
        if ref_date and contract.get("execution_date"):
            if _dates_match(ref_date, contract["execution_date"]):
                score += 0.3
                reasons.append("referenced date matches execution date")
        if score > 0:
            scored.append(
                (
                    score,
                    {
                        "contract_id": contract["id"],
                        "contracting_party": contract["contracting_party"],
                        "agreement_type": contract["agreement_type"],
                        "execution_date": contract["execution_date"],
                        "confidence": round(min(score, 1.0), 2),
                        "reason": ", ".join(reasons),
                    },
                )
            )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in scored]


def _save_to_dropbox(
    data_dir: Path,
    staging: dict[str, Any],
    party: str,
    folder: str | None = None,
) -> dict[str, Any]:
    """Upload the staged original to Dropbox. Never fails the commit.

    ``folder`` pins the destination folder so a follow-on lands beside its parent.
    """
    if not dropbox_store.is_configured(data_dir):
        return {"saved": False}
    stored = files.resolve(data_dir, staging.get("storage_key") or "")
    if stored is None:
        return {"saved": False}
    try:
        result = dropbox_store.save_contract_file(
            data_dir,
            stored,
            party,
            staging.get("source_filename") or stored.name,
            folder=folder,
        )
        return {"saved": True, "link": result["link"], "path": result["path"]}
    except DropboxError as exc:
        return {"saved": False, "error": str(exc)}


def _followon_name(parsed: dict[str, Any]) -> str:
    """A short display name for an attached follow-on document."""
    doc_type = parsed.get("agreement_type") or "Amendment"
    source = parsed.get("source_title") or ""
    match = re.search(r"(No\.?\s*\d+|#\s*\d+)", source)
    return f"{doc_type} {match.group(1)}" if match else doc_type


def _normalize(name: str) -> str:
    lowered = name.lower()
    lowered = re.sub(r"[.,]", "", lowered)
    lowered = re.sub(
        r"\b(llc|inc|incorporated|corporation|corp|ltd|limited|llp|lp|co)\b",
        "",
        lowered,
    )
    return re.sub(r"\s+", " ", lowered).strip()


def _dates_match(a: str, b: str) -> bool:
    """Loose date comparison tolerant of format differences."""
    da, db = _digits(a), _digits(b)
    if da and db and da == db:
        return True
    return bool(a and b and a[:7] == b[:7])


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


# ---- staging persistence -------------------------------------------------


def _staging_path(data_dir: Path, analysis_id: str) -> Path:
    return data_dir / "staging" / f"{analysis_id}.json"


def _write_staging(data_dir: Path, analysis_id: str, staging: dict[str, Any]) -> None:
    path = _staging_path(data_dir, analysis_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(staging))


def _claim_staging(data_dir: Path, analysis_id: str) -> dict[str, Any]:
    """Atomically take ownership of a staging record so it commits exactly once.

    The rename is atomic, so if two commits race (e.g. a double-click), only the
    first succeeds; the second sees the file already gone and raises -- preventing
    a duplicate contract.
    """
    path = _staging_path(data_dir, analysis_id)
    claimed = path.with_suffix(".claimed")
    try:
        path.rename(claimed)
    except FileNotFoundError as exc:
        raise StagingError(
            "This contract was already added (or the analysis expired). "
            "Return to search to see it, or upload the file again."
        ) from exc
    data = json.loads(claimed.read_text())
    claimed.unlink(missing_ok=True)
    return data
