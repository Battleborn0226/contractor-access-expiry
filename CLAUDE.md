# Contractor Access Expiry

Read-only GitHub App: finds outside collaborators with stale access and prices
the licensed seats they consume. A 30-day falsification test of whether
Marketplace delivers installs without promotion. Full context in README.md.

## Shared memory with ChatGPT/Codex

Brief before answering anything substantive:

```
C:\Users\Max02\.boardroom\boardroom.cmd --thread business brief --for claude
```

Log what gets settled:

```
C:\Users\Max02\.boardroom\boardroom.cmd --thread business log claude "..."
```

Use the `business` thread — this project came out of that debate and the
reasoning behind every constraint is recorded there.

## Non-negotiables

- **No write permissions, ever, during the pilot.** No code path may add,
  change, or remove access. Removing an outside collaborator from a private
  repo permanently deletes their private forks. The read-only claim is on the
  listing and in the UI.
- **Seats count per person, not per grant.** One outside collaborator with ten
  private repos is one licensed seat. Counting grants inflates the headline
  saving into fiction.
- **Never assert someone is inactive.** Report absence of commit activity.
  `no_commits` is a separate state from `stale`. A PR reviewer leaves no
  commits.
- **Never overstate a saving.** Unknown plan falls back to the cheaper Team
  rate. Estimates are labelled as estimates against the customer's invoice.
- **Do not touch the gate thresholds in `storage.py`.** They were fixed before
  the data existed. Moving them to fit results is the failure this design
  exists to prevent.

## Conventions

- Python 3.13, `.venv\Scripts\python.exe`, pytest in `tests/`
- `from __future__ import annotations` at the top of every module
- Credentials from the environment only. Never write a key into a file, never
  accept one pasted into chat — Max registers the App himself.
- `tools/preview_report.py` renders the report with no GitHub access; use it
  for layout checks and listing screenshots.
