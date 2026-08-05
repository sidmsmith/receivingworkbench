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
  Qty, UOM — quantities are converted from base units into each item's
  actual receiving UOM (Case, Pack, Pallet, etc.) via its `ItemPackage[]`
  conversion table, not shown as raw base-unit counts.
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

**Known open items** (see comments in `rw_service.py`/`mawm_client.py` for
detail):
- No handling yet for MAWM rejecting/warning when a receive quantity
  exceeds an item's max LPN quantity.
- A receive write and the very next ASN read can lag by roughly one
  receive's worth of quantity (eventual consistency) — low risk today since
  the busy overlay blocks rapid double-clicks on the same line, but not
  formally mitigated (no poll-until-visible/retry-with-reverify).

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
