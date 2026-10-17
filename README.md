<p align="center">
  <img alt="Contract Tracker" src="template.svg" width="480">
</p>

# Contract Tracker

<p align="center">
  <a href="https://studio.imbue.com/open?git_url=https://github.com/nicholasgjokaj-star/contract-tracker"><img alt="Open in Imbue Studio" height="64" src="https://img.shields.io/badge/Open%20in%20Imbue%20Studio-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Studio workspace and paste this to your agent:
` /use-template https://github.com/nicholasgjokaj-star/contract-tracker`

## Why you care

Upload contracts, let Claude extract the key terms, then organize, search and tag them, with optional Dropbox and Notion sync.

Signed contracts tend to scatter across inboxes, shared drives and folders, and the
details that matter -- who it's with, when it ends, what it's worth -- stay locked
inside PDFs. Contract Tracker gives you one searchable register: drop in a contract
and Claude reads it for you, so renewal dates and obligations stop slipping by.

## How to use it

Open the **Contracts** window on your desktop.

1. **Upload a contract** (PDF or text). AI reads it and fills in the
   contracting party, agreement type, execution and termination dates,
   termination terms, estimated value (and whether that value is calculated or
   a best guess), and a short description.
2. **Review before saving.** The extracted fields are shown for you to check and
   correct; pick the department and owner, then save.
3. **Follow-on documents stay together.** Amendments, addenda and statements of
   work are automatically recognized as follow-ons and attached to the original
   agreement's profile instead of becoming separate rows.
4. **Find anything.** Search across party, description, department and type;
   filter by department and status (Active / Terminated); add tags and notes.
5. **Open the original.** Every contract keeps its source file and the raw
   extracted text, one click from its profile.
6. **Optional sync.** Mirror the register into a Notion database with one button,
   and save originals into Dropbox folders named after each counterparty.

## Ideas for making it yours

- Add renewal reminders: a weekly note listing contracts that terminate in the
  next 60 days.
- Teach the extraction to pull fields you care about -- payment terms, governing
  law, auto-renewal clauses -- and add them as columns.
- Swap Dropbox for your own document store, or link each contract to its
  e-signature envelope.
- Add a spend view that totals estimated value by department or by year.

## What this is

This repository is a published **Imbue Studio template**: a clean, bootable
snapshot of what an agent built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
