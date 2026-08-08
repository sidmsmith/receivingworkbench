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

## Warning message handling — detection/UI done; real override contract now known (2026-08-08)

Detection + UI (items 1-4) are implemented: `receive_lpn()`
(`mawm_client.py`) always parses the response body now and only
raises on a genuinely unparseable one; `extract_warning()`/
`extract_message()` were ported from `taskcompletion/mawm_client.py`;
`rw_service.receive_line()` detects a WARNING and returns `{warning:
True, messageId, messageText, lpnId, ...}` instead of treating it as
success; `public/app.js` has a Confirm/Cancel `warningModal` plus
`receiveLineWithWarningHandling()`, a retry loop that reuses the same
generated iLPN. Live-confirmed against `ASN0000003` line 1 (item
`50002215`): the core `receiving/lpn/receive` endpoint *does* surface
the warning via the standard `messages.Message[]` envelope
(`Code: "RCV::995"`, `Type: "WARNING"`, `Description: "Item Missing
Critical Dims, take to Cubiscan Station."`) over a non-2xx
(`BAD_REQUEST`) status — confirming the parse-before-raise fix above
was the right call.

**Item 5, the override contract, is now CONFIRMED from a real HAR
capture** (`missingdimensions.har`, a live RF Receiving session
against `ASN0000003`/item `50002215`, the same `RCV::995` warning) —
**but it is NOT wired into `receive_lpn()` yet; that's still open.**
The finding: unlike Putaway, real RF Receiving does *not* go through
the core `receiving/lpn/receive` endpoint at all. It drives a
stateful **DMM Mobile Facade "Receiving" workflow**, structurally
identical to Putaway's:

1. `POST dmmobile-facade/api/dmmobile-facade/workflow/init?transactionId=Receiving&transactionType=Receive`
   (empty body) → `header.currentState: "AcceptASN"`.
2. `POST .../workflow/execute/workflowScriptName/Receiving/stateName/AcceptASN/actionName/AcceptASN`
   with `state.ASNId` set to the scanned ASN → `currentState: "AcceptStagingLocation"`.
3. `.../AcceptStagingLocation/actionName/AcceptStagingLocation` with
   `state.EnteredLocation` set → `currentState: "AcceptLPN"`.
4. `.../AcceptLPN/actionName/AcceptLPN` with `state.LPNId` — **blank
   string submitted, not a generated id** — the server auto-assigns
   one itself (response came back `LPNId: "LPN09159"`, i.e. this
   workflow generates its own iLPN as part of the step; don't
   pre-call `generate_ilpn_ids()` for this path) → `currentState:
   "AcceptItem"`.
5. `.../AcceptItem/actionName/AcceptItem` with `state.itemBarcode` set
   to the scanned item — **this is where `RCV::995` fired**, 400,
   `state.errorVOList: [{errorCode: "RCV::995", errorCategory:
   "WARNING", errorMessage: "Item Missing Critical Dims, take to
   Cubiscan Station.", componentName: "com-manh-cp-receiving"}]`,
   `warningOverrideList: []` on the way in.
6. **The override, confirmed live**: resubmit the *entire* `workflowVO`
   from step 5's response, unchanged except
   `state.warningOverrideList: ["RCV::995"]` added (state.errorVOList
   left as-is from the error response) — same action/URL as step 5.
   Response: 200, `errorVOList: []`, `warningOverrideList:
   ["RCV::995"]` echoed back, `currentState: "AcceptQuantity"`. This
   is the exact same mechanism `taskcompletion` already confirmed for
   Putaway (see below) — `apply_warning_overrides()` there is
   directly reusable, no receiving-specific change needed to that
   function itself.
7. `.../AcceptQuantity/actionName/AcceptQuantity` with
   `state.EnteredQuantity.scannedQuantity1` set → `currentState`
   loops back to `"AcceptLPN"` (ready for the next item/LPN in the
   same receiving session) with `errorAction:
   "ItemVerificationRuleEvaluation"` in the header.

Every step resubmits the *entire* `workflowVO` (whatever the previous
response returned), mutating only the one field that step is for —
identical discipline to Putaway's flow. Headers in the HAR are
browser-session/cookie-based (`x-xsrf-token`, no `Authorization`
header visible) since it's a live UI capture, not this app's Bearer
token — irrelevant either way, since `taskcompletion.build_task_headers()`
already proved the Bearer-token path works against this same DMM
Mobile Facade for Putaway; reuse it rather than trying to match the
HAR's browser headers literally.

**Not yet decided/implemented**: whether to (a) replace
`receive_line()`'s core-API call with this DMM workflow outright, or
(b) keep the core `receiving/lpn/receive` call as the fast path for
the normal (no-warning) case and only drop into this workflow as a
fallback when a warning is detected, mirroring how taskcompletion
ended up structuring Putaway (core call first; DMM flow specifically
for the override). (b) is very likely the right call — smaller blast
radius on the already-extensively-confirmed golden path — but wasn't
committed to code as of this entry; don't assume it's done.

Read `taskcompletion/CLAUDE.md` in full before implementing; short
version of how Putaway solved the equivalent problem (same shape as
above, kept here for the prior context that led to the HAR capture):

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
  on core API responses, or `workflowVO.header.state.errorVOList`
  entries with `errorCategory == "WARNING"` on the DMM flow. See
  `taskcompletion/mawm_client.py`'s `extract_warning()` — receiving's
  own port only checked the first shape until now; it needs the
  `errorVOList` branch added back if/when the DMM flow is wired in.
- **Override — CONFIRMED working for the DMM Mobile Facade flow, both
  Putaway and now Receiving**: mutate
  `workflowVO.header.state.warningOverrideList` to include the
  warning's `errorCode`, then resubmit the *entire* `workflowVO`
  object unchanged otherwise, to the same action. No server-side
  session needed — the whole state round-trips through the caller.
  See `taskcompletion/mawm_client.py`'s `apply_warning_overrides()`
  and `task_service.py`'s `complete_container_putaway()` for the
  retry loop shape to mirror.
- **Override — CONFIRMED NOT working on a plain core-API endpoint**:
  a guessed `userInputs: {code: code}` override on
  `putaway/api/putaway/execution/container/move` returned the
  identical warning again. Consistent with the new finding above:
  receiving's core `receiving/lpn/receive` endpoint was never even
  the right place to look for an override — real RF Receiving doesn't
  use it for this at all.
- **Frontend pattern to mirror**: a required Confirm/Cancel modal
  showing the warning's `messageId`/`messageText`, and a retry loop
  that accumulates confirmed warning codes into an override map and
  resubmits until no warning comes back. Already implemented here —
  see `public/app.js`'s `showWarningModal()`/
  `receiveLineWithWarningHandling()` — but its retry currently
  resubmits the same (non-working) core-API call; once the DMM
  fallback exists server-side, the frontend contract (`warning`/
  `messageId`/`messageText`/retry-with-`lpnId`) shouldn't need to
  change, only what `rw_service.receive_line()` does internally on a
  detected warning.
- Also worth reusing: `extract_message()` (prefers
  `messages.Message[].Description` over a generic top-level
  `message`/`messageKey`) so a real business message surfaces instead
  of a generic `"error.400"` when a warning can't be cleared.
