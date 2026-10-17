---
title: "Contract Tracker"
description: "Upload contracts, let Claude extract the key terms, then organize, search and tag them, with optional Dropbox and Notion sync."
thumbnail: "template.svg"
version: v1
format: v2
---

# Contract Tracker

This file is the manifest for the **Contract Tracker** template (slug:
`contract-tracker`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a workspace that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

Upload contracts, let Claude extract the key terms, then organize, search and tag them, with optional Dropbox and Notion sync.

Contract Tracker is a contract register for a small organization. The user uploads
a signed contract (PDF or text); Claude extracts the contracting party, agreement
type, execution and termination dates, termination terms, estimated value (with
whether it is calculated, a best guess, or manually set), and a brief description.
The user reviews and corrects the extraction, assigns a department and owner, and
saves it. Amendments, addenda and statements of work are detected as follow-ons and
attached to their parent agreement. The running app is a single window with a
searchable, filterable table of contracts and a profile page per contract holding
its documents, tags, notes, the original file and the raw extracted text.
Optionally, each record is mirrored to a Notion database and each original file is
saved to Dropbox in a folder per counterparty.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original agent onto a clean default-workspace-template base):

- `system/apps/contracts`
- `system/supervisord.conf.d/contracts.conf`

- `system/apps/contracts` -- the app: a Flask server (`runner.py`) with a
  single-page front end (`assets/`). `db.py` is the SQLite store (contracts,
  documents, notes, tags, plus the raw extracted text and verbatim AI
  extraction for every record). `extract.py` reads PDFs with pdfplumber and asks
  Claude for a structured JSON extraction through `claude_p.py` (headless
  `claude -p`). `service.py` runs the analyze -> review -> commit flow and
  follow-on linking. `files.py` keeps originals locally; `dropbox_store.py`,
  `notion.py` / `notion_client.py` and `drive.py` are the optional
  integrations.
- `system/supervisord.conf.d/contracts.conf` -- the supervisord program
  `contracts`. It registers port 8200 as the "contracts" window via
  `system/scripts/forward_port.py --manifest system/apps/contracts/app.toml`,
  then runs the app's `contracts` console script.

All state lives under `data/.apps/contracts/` (the runner's `DATA_DIR`
default): `contracts.db`, stored originals in `files/`, staged uploads awaiting
review in `staging/`, and the Dropbox (`dropbox.json`) and Notion
(`notion.json`) settings. That directory starts empty in a new workspace.

Configuration is by environment variable on the `contracts` program:
`CONTRACTS_OUR_COMPANY` (your legal entity; used in the extraction prompt and as
the default signing entity) and `CONTRACTS_DRIVE_FOLDER_ID` (optional Google
Drive folder). The department list is the `DEPARTMENTS` constant in `db.py`.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting agent must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.


Activation (Notion and Drive are optional integrations; the core register works
without either, but the adopting agent should still offer them during setup):

- requires_permission: notion-mcp-api / notion-mcp-read-all (Notion sync reads the mirror database through Notion's hosted MCP server)
- requires_permission: notion-mcp-api / notion-mcp-write-all (Notion sync creates the mirror database and creates/updates one row per contract)
- requires_permission: google-drive-api / google-drive-read-all (optional: lists and links files in the configured Drive agreements folder)
- requires_llm: calls Claude via the keyless subscription path (headless `claude -p`, see `claude_p.py`) to extract contract terms; an adopter on the keyed path (ANTHROPIC_API_KEY / litellm) must switch the call in `extract.py` per use-ai-integration

Adaptation:

- **Your company name.** The extraction prompt needs to know which side of the
  contract is "us". Set `CONTRACTS_OUR_COMPANY` on the `contracts` program to
  the adopter's legal entity; until then the placeholder "Your Company, Inc." is
  used and extraction may misidentify the contracting party.
- **Departments.** `DEPARTMENTS` in `db.py` is a generic list (Marketing,
  Engineering, Finance, Legal, Operations, People). Replace it with the
  adopter's own.
- **Dropbox (optional).** Dropbox is called directly, not through latchkey,
  because latchkey's Dropbox connector was unreliable when this was built. To
  enable it, create a Dropbox app, and write `access_token` (and, for automatic
  refresh, `refresh_token` and `app_key` from the OAuth offline flow) into
  `data/.apps/contracts/dropbox.json`. Without it, originals are kept locally
  only.
- **Google Drive folder (optional).** Set `CONTRACTS_DRIVE_FOLDER_ID` to the
  folder that holds signed agreements; while unset, Drive features are off.
- **Notion database.** The first sync creates a new "Contracts" database in the
  connected Notion workspace and remembers it in `notion.json`; nothing to
  configure beyond the permission above.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting agent
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that agent's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. (PDF reading uses the
pdfplumber Python package, installed from the app's own dependencies.)

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new agent. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the Imbue Studio app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-10-16) -- first published: the Contract Tracker app with Claude extraction, search, follow-on documents, and optional Notion, Dropbox and Drive sync

## Adaptation history

Each agent that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
