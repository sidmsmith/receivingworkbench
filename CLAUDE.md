# Receiving Workbench — Project Instructions

## Commit, push, and verify deploy after large updates

After any large/notable update to this project (new feature, non-trivial
bug fix, meaningful UI/behavior change):

1. Bump the version by 0.0.1 (patch digit) in whichever of these actually
   changed: `public/index.html` (the `<title>` — "Receiving Workbench
   vX.Y.Z"), `api/index.py` (`APP_VERSION`), `package.json` (`version`).
   This lets the user glance at the deployed page and confirm a Vercel
   deploy actually picked up recent work, without checking commit
   hashes/timestamps. Keep them in sync when a change touches more than
   one; only bump the file(s) you actually changed otherwise.
2. Commit the change with a descriptive message (do not amend/force-push).
3. Push to `origin main`.
4. Wait ~20 seconds, then check the Vercel deployment status (via the
   Vercel MCP tools — `list_deployments`/`get_deployment`, or
   `list_projects` first if the project mapping isn't already known) and
   report back whether the deploy succeeded — do this unprompted, without
   being asked.

Small/trivial edits (typos, comment tweaks, local-only experiments) don't
need this — use judgment, and ask if unsure whether a change counts as
"large."

## Secrets

- Never commit `.token`, credentials, or other gitignored secrets.
- Never force-push or amend unless the user explicitly asks.
