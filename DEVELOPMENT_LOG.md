## Warning message handling — fully implemented and CONFIRMED live end-to-end (2026-08-08)

Detection, UI, and the actual override are all done. `receive_lpn()`
(`mawm_client.py`) always parses the response body now and only
raises on a genuinely unparseable one; `extract_warning()`/
`extract_message()` were ported from `taskcompletion/mawm_client.py`
(both the core-API `messages.Message[]` shape and the DMM
`workflowVO.header.state.errorVOList` shape); `rw_service
.receive_line()` detects a WARNING and returns `{warning: True,
messageId, messageText, lpnId, ...}` instead of treating it as
success; `public/app.js` has a Confirm/Cancel `warningModal` plus
`receiveLineWithWarningHandling()`, a retry loop that folds a
confirmed warning code into `warningOverrides` and resubmits.

The override itself — item 5, the piece that was open — is
**CONFIRMED live via a real HAR capture** (`missingdimensions.har`, a
live RF Receiving session against `ASN0000003`/item `50002215`,
`RCV::995` "Item Missing Critical Dims") **and CONFIRMED again via
this app's own implementation, live against `ASN0000003`, both
directly via curl and end-to-end through the actual browser UI**:
Received Qty visibly ticked up (3→4 UNIT) and a new LPN appeared in
Received LPNs immediately after clicking Confirm on the warning modal
— no code path left as a documented no-op. `rw_service
.receive_via_dmm_workflow()` is the implementation:
`receive_line()` takes the fast core-API path (`receive_lpn()`,
unchanged) for the normal no-warning case, and only when a retry
carries `warning_overrides` does it skip straight to
`receive_via_dmm_workflow()` — the DMM path is never used unless a
warning was already seen and confirmed once.

**Two edge cases remain genuinely unconfirmed** (flagged in
`receive_via_dmm_workflow()`'s docstring, not guessed away):
- Whether `AcceptStagingLocation`'s `EnteredLocation` can be submitted
  blank when no staging location was entered — the HAR and this app's
  own live test both had a real one set (`STAGIB0201`). If this
  ReceivingCriteria genuinely requires one
  (`LocateLpnToStagingId: "PROMPT_FOR_STAGING_LOCATION"`), a blank
  submission should surface as a real (non-warning) error via
  `extract_message()`, not silently fail — but this hasn't been
  exercised.
- Whether `AcceptQuantity`'s `EnteredQuantity.scannedQuantity1` wants
  base units or the item's display/pack UOM — every confirmed test
  used a UNIT-uom item (factor 1, so the two are indistinguishable).
  Passed as base units for consistency with `receive_lpn()`'s own
  contract; revisit if this path is ever hit for a pack/case item.

The rest of this section is the original HAR-derived field mapping
that led to the implementation above — kept for reference, not
because anything in it is still open:

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

**Implemented as (b)**: the core `receiving/lpn/receive` call
(`receive_lpn()`) stays the fast path for the normal (no-warning)
case, untouched; `receive_via_dmm_workflow()` is only invoked as a
fallback once `warning_overrides` is non-empty on a retry — mirroring
how taskcompletion structured Putaway. Smaller blast radius on the
already-extensively-confirmed golden path, as expected.

`taskcompletion/CLAUDE.md` is still worth reading for the prior
context that led to the HAR capture and this implementation:

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
  own `mawm_client.extract_warning()` now checks both shapes too.
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
  resubmits until no warning comes back. Implemented here —
  `public/app.js`'s `showWarningModal()`/
  `receiveLineWithWarningHandling()` — and needed no changes when the
  DMM fallback was wired in server-side, as predicted: the frontend
  contract (`warning`/`messageId`/`messageText`/retry-with-`lpnId`)
  stayed the same, only what `rw_service.receive_line()` does
  internally on a detected warning changed.
- Also worth reusing: `extract_message()` (prefers
  `messages.Message[].Description` over a generic top-level
  `message`/`messageKey`) so a real business message surfaces instead
  of a generic `"error.400"` when a warning can't be cleared.
