# Receiving Workbench — Project Instructions

This project follows the global `AGENTS.md` and `SECURITY_BASELINE.md`.
The notes below cover only what's specific to this repository.

## Version identifiers

This project's version appears in three places — bump whichever
actually changed:

- `public/index.html` — the `<title>` ("Receiving Workbench vX.Y.Z")
- `api/index.py` — the `APP_VERSION` constant
- `package.json` — the `version` field

Keep them in sync when a change touches more than one.

## Deploy verification

After pushing, wait ~20 seconds before checking Vercel deployment
status — the build needs a moment to complete. If the Vercel project
mapping isn't already known, look it up with `list_projects` first.
