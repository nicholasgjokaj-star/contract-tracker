"""Turn an uploaded contract document into structured data with Claude.

Two steps: pull the text out of the document (PDF today; plain-text fallback),
then ask Claude to extract the fields the search view and profile need -- parties,
dates, an estimated value flagged as calculated-vs-best-guess, a short
description, termination terms, the agreement type, and any exhibits / statements
of work / payment schedules bundled inside.

This workspace has no ``ANTHROPIC_API_KEY`` configured, so we take the keyless
path and call Claude through the vendored ``claude -p`` helper (see the
use-ai-integration skill). That helper resolves the workspace's credentials with
``read_workspace_ai_credentials`` -- the ``data/.secrets/anthropic.env`` snapshot,
then the shared Claude settings, then the process env -- rather than reading the
process environment alone, which a supervisord service inherits frozen at boot.
If a key is ever configured here, switch this call to litellm per that skill. The
verbatim model output is preserved by the caller alongside the record.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pdfplumber

from contracts.claude_p import ClaudeCLIError, claude_p_completion
from contracts.db import OUR_COMPANY

# Contract extraction is legal-document reasoning, not simple classification, so
# we use a stronger model than the helper's default.
_MODEL = "claude-sonnet-5"

_SYSTEM = f"""You are a contracts analyst for {OUR_COMPANY}. You read a single
contract document and return a strict JSON object describing it. {OUR_COMPANY}
(including any of its affiliates or trade names) is "us"; the other signatory is
the "contracting party". Only return the JSON object, no prose, no code fences."""

_INSTRUCTIONS = """Extract the following as a single JSON object with exactly these keys:

- "agreement_type": one of "Agreement", "Amendment", "Addendum",
  "Statement of Work", "Exhibit", "Other". Classify by what the document IS.
- "is_followon": true if this document modifies or attaches to a pre-existing
  agreement (an amendment, addendum, or additional statement of work); false for
  a standalone/original agreement.
- "contracting_party": the party that is NOT us (company or person).
- "our_entity": our entity that signed (e.g. "{our_company}").
- "execution_date": the date the agreement was made/executed, as "YYYY-MM-DD";
  null if none is stated.
- "termination_date": the date it terminates/expires, as "YYYY-MM-DD"; null if
  open-ended or none is stated.
- "termination_terms": a plain-language summary of how and when it can be
  terminated (notice periods, auto-termination triggers). Empty string if none.
- "est_value": a human-readable total contract value, formatted like "$120,000".
  For hourly/open-ended deals give your best-guess total.
- "est_value_amount": the numeric value in USD (no symbols/commas), or null.
- "est_value_is_guess": true if the total is NOT a fixed figure stated in the
  document and you had to estimate it (e.g. hourly rate with no cap); false if the
  document states a fixed total or fee you simply read off.
- "est_value_basis": one sentence explaining how you got the value (e.g. "Fixed
  project fee stated in Exhibit B" or "Estimated: $100/hr x 20 hrs/week x ~6
  months").
- "description": a 2-3 sentence neutral summary of what the contract is for.
- "referenced_agreement": for a follow-on, an object {"party","date","title"}
  describing the original agreement it references; otherwise null.
- "changes": for an amendment/addendum, a plain-language summary of what it
  changes (scope, fees, dates); empty string otherwise.
- "documents": an array of the exhibits, statements of work, payment schedules,
  or addenda contained WITHIN this document. Each item is an object with:
    - "doc_type": "Statement of Work", "Exhibit", "Amendment", or "Addendum"
    - "name": e.g. "Exhibit B - Compensation"
    - "summary": one line describing it
    - "details": an object with any structured content you can pull out, e.g.
      {"payment_schedule": [{"milestone":"...","fee":"$24,000"}], "full_text":"..."}.
      Include a "full_text" key with the section's text when it is short enough.

Return ONLY the JSON object.

CONTRACT DOCUMENT TEXT:
"""


def extract_text(file_path: Path) -> str:
    """Pull text out of a document. PDF via pdfplumber; else read as UTF-8 text."""
    if file_path.suffix.lower() == ".pdf":
        parts: list[str] = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                parts.append(page.extract_text() or "")
        return "\n\n".join(parts).strip()
    return file_path.read_text(encoding="utf-8", errors="replace").strip()


def parse_contract(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask Claude to extract structured fields from contract text.

    Returns ``(parsed_fields, meta)`` where ``meta`` carries the raw model text
    and the reported cost/usage so the caller can preserve and surface it.
    """
    if not text.strip():
        raise ExtractionError("The document appears to be empty or unreadable.")

    prompt = _INSTRUCTIONS.replace("{our_company}", OUR_COMPANY) + text[:120_000]
    try:
        result = claude_p_completion(prompt, system=_SYSTEM, model=_MODEL)
    except ClaudeCLIError as exc:
        # A model/CLI failure should surface as a clean upload error (the route
        # maps ExtractionError to a 422), not an uncaught 500.
        raise ExtractionError(f"The AI could not analyze this document: {exc}") from exc
    parsed = _parse_json(result.text)
    meta = {
        "raw_model_output": result.text,
        "cost_usd": result.cost_usd,
        "model": _MODEL,
    }
    return parsed, meta


class ExtractionError(RuntimeError):
    """The document could not be read or the model output could not be parsed."""


def _parse_json(raw: str) -> dict[str, Any]:
    """Parse the model's JSON, tolerating stray code fences or surrounding text."""
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("```", 2)[1]
        if candidate.startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(candidate[start : end + 1])
            except json.JSONDecodeError as exc:
                raise ExtractionError(
                    f"Could not parse the AI's response as JSON: {exc}"
                ) from exc
        raise ExtractionError("The AI did not return a JSON object.")
