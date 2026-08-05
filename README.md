# Receiving Workbench

A desktop MAWM app for a warehouse dock worker to receive against an ASN.

## Current scope

- Org authentication (mirrors `supplierenablement`'s `.token`-file-first, then
  `MANHATTAN_PASSWORD`/`MANHATTAN_SECRET` OAuth flow).
- Preloads all ASNs eligible for receiving (`AsnStatus` In Transit / In
  Receiving) into a client-side cache after auth.
- Scan/type a single ASN number — "Load ASN" enables only when it matches a
  preloaded, receivable ASN.
- Loads the ASN and displays its lines: Line #, Item (with a small item
  image thumbnail — hover to enlarge), Description, Shipped Qty, Received
  Qty. Quantities are formatted exactly like MAWM's own UI (verified against
  a live RF session capture and MAWM's own ASN-line report, both provided
  by the user) — converted from base units into the item's pack UOM (Case,
  Pack, Pallet, etc.) via its `ItemPackage[]` conversion table, shown as a
  mixed "{packs} {uom} {remainder} units" string when it doesn't divide
  evenly (e.g. "0 packs 1 units"), just the pack count when it does (e.g.
  "3 Case"), and blank (not "0 <uom>") when nothing's been received yet.
  Received quantity itself is summed from dcinventory's Inventory object
  (`OnHand` per receive-created LPN) — the ASN's own nested
  `Lpn[].LpnDetail[]` quantity fields were tried first and found unreliable
  for a receive of more than 1 unit in a single call (see
  `rw_service._received_qty_by_asn_line`'s docstring for the full story).
- Click a line to select it, then:
  - **Full Line** — receives the entire remaining quantity, no confirmation.
  - **Partial Line** — modal to choose a quantity (defaults to, capped at,
    the remaining quantity).
  - **All Lines** — confirms, then receives the full remaining quantity of
    every outstanding line, one `lpn/receive` call per line.
- `TransactionId`/`ReceivingStrategy` are hardcoded (`"Receiving"` /
  `"Receiving Strategy"`) — a dropdown to choose these is planned, not built.
- URL boot params (mirrors `supplierenablement`/`vasexecution`, case-insensitive):
  `org`/`organization` auto-authenticates; `asn`/`asnid`/`asn_id`/`asn-id`
  deep-links straight to a loaded ASN once auth completes; `theme=<key>`
  pre-selects a theme, `theme=N` hides the theme picker button.
- Collapsed accordion below the lines table, "Received LPNs (N)", listing
  every LPN linked to the ASN: LPN, Status (`Lpn[].LpnStatus`, e.g.
  "Received"), Location (`CurrentLocationId` — blank until put away, since
  this app's own receives don't pass one), Item ("MIXED" if the LPN holds
  more than one), Description (blank if mixed), Qty + UOM (blank if mixed),
  Condition Code (comma-separated if more than one). Condition codes
  required their own endpoint — `dcinventory/containerCondition/search` —
  since neither `ilpn/search` nor `inventory/search` expose them despite
  `lpn/receive`'s own response echoing one back.

**Known open items** (see comments in `rw_service.py`/`mawm_client.py` for
detail):
- No handling yet for MAWM rejecting/warning when a receive quantity
  exceeds an item's max LPN quantity.
- Possible eventual-consistency lag between a receive write and the very
  next read — not re-verified against the current (corrected)
  dcinventory-based received-quantity source, not formally mitigated
  (no poll-until-visible/retry-with-reverify). Low risk today since the
  busy overlay blocks rapid double-clicks on the same line.

## Running locally

Requires a `-DEMO` org token, either via:
- A local `.token` file (gitignored) containing a raw Bearer token, or
- `MANHATTAN_PASSWORD` / `MANHATTAN_SECRET` env vars (OAuth).

```
npm install
pip install -r requirements.txt
vercel dev
```

or, running the two processes separately:

```
python api/index.py        # Flask API on :5000
node server.js              # static + proxy on :3011
```

## Test data

`ASN000000000013` is a known-good ASN in the `-DEMO` org for manual testing.
