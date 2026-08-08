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

## Warning message handling — detection/UI done, override still blocked (2026-08-08)

Items 1-4 below are implemented: `receive_lpn()` (`mawm_client.py`)
always parses the response body now and only raises on a genuinely
unparseable one; `extract_warning()`/`extract_message()` were ported
from `taskcompletion/mawm_client.py`; `rw_service.receive_line()`
detects a WARNING and returns `{warning: True, messageId, messageText,
lpnId, ...}` instead of treating it as success; `public/app.js` has a
Confirm/Cancel `warningModal` plus `receiveLineWithWarningHandling()`,
a retry loop that reuses the same generated iLPN (via the new
`lpnId`/`warningOverrides` params threaded through
`api/index.py`'s `/api/receive_line` → `rw_service.receive_line()` →
`mawm_client.receive_lpn()`) rather than minting a fresh one per
retry attempt.

**Still open — item 5, the actual override contract.** `receive_lpn()`
accepts `warning_overrides` but deliberately does nothing with it yet:
there's no known override mechanism for a plain core-API endpoint like
`receiving/lpn/receive` (see below — the DMM shape doesn't carry over,
and the obvious guess is confirmed wrong on a similar core endpoint).
Until the real contract is known, `receiveLineWithWarningHandling()`
guards against looping forever on an unclearable warning: if the exact
same `messageId` comes back a second time after already being
confirmed once, it stops and surfaces a plain "could not be cleared"
error instead of reshowing the modal. Once a HAR capture (see below)
reveals the real request shape, wire it into `receive_lpn()` where the
`# warning_overrides is intentionally not applied to the payload yet`
comment is.

Read `taskcompletion/CLAUDE.md` in full before touching the override
side; short version of how Putaway solved the equivalent problem:

- **The bug fixed first was the same class of bug**: MAWM returns at
  least some warnings over a non-2xx HTTP status. Raising early throws
  the warning away before it can be inspected. See
  `taskcompletion/mawm_client.py`'s `fetch_putaway_move()`/
  `commit_putaway_move()`/`workflow_init()`/`workflow_execute()` for
  the pattern, and that repo's CLAUDE.md entry "Bug fixed 2026-08-08"
  for the full story of how this exact mistake looked in practice
  (a warning rendering as a raw red error instead of a Confirm/Cancel
  modal).
- **Detection**: a `messages.Message[]` envelope (`Type == "WARNING"`)
  on core API responses, or — if this ever needs a DMM Mobile Facade
  flow the way Putaway did — `workflowVO.header.state.errorVOList`
  entries with `errorCategory == "WARNING"`. See
  `taskcompletion/mawm_client.py`'s `extract_warning()`.
- **Override — CONFIRMED working, but only for the DMM Mobile Facade
  flow**: mutate `workflowVO.header.state.warningOverrideList` to
  include the warning's `errorCode`, then resubmit the *entire*
  `workflowVO` object unchanged otherwise, to the same action. No
  server-side session needed — the whole state round-trips through the
  caller. See `taskcompletion/mawm_client.py`'s
  `apply_warning_overrides()` and `task_service.py`'s
  `complete_container_putaway()` for the retry loop.
- **Override — CONFIRMED NOT working**: a guessed `userInputs: {code:
  code}` override on a core-API endpoint
  (`putaway/api/putaway/execution/container/move`) — resubmitting with
  that shape returned the identical warning again. **This means
  there's no known override mechanism yet for a plain core-API
  endpoint like `receiving/lpn/receive`.** Don't assume the DMM
  Putaway override shape carries over to receiving's core endpoint —
  the state object shape and workflow states are domain-specific
  (`PutawayVO` vs. whatever receiving's equivalent is), and it was
  only ever learned by capturing a real HAR of the actual mobile RF
  client. The concrete next step here, when ready, is the same one
  that unblocked Putaway: get a HAR capture of a real RF session
  triggering a receiving warning (e.g., exceeding `MaxLpnQuantity`),
  then read the request/response bodies directly (not just headers —
  see taskcompletion's CLAUDE.md note that headers were a dead end
  there; the actual bug turned out to be in the request body shape)
  to learn the real override contract for this domain.
- **Frontend pattern to mirror**: a required Confirm/Cancel modal
  showing the warning's `messageId`/`messageText`, and a retry loop
  that accumulates confirmed warning codes into an override map and
  resubmits until no warning comes back. See
  `taskcompletion/public/app.js`'s `showWarningModal()` and
  `completeLineWithWarningHandling()`.
- Also worth reusing: `extract_message()` (prefers
  `messages.Message[].Description` over a generic top-level
  `message`/`messageKey`) so a real business message surfaces instead
  of a generic `"error.400"` when a warning can't be cleared.
