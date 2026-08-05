# Receiving Workbench

A desktop MAWM app for a warehouse dock worker to receive against an ASN.

## Phase 1 scope (current)

- Org authentication (mirrors `supplierenablement`'s `.token`-file-first, then
  `MANHATTAN_PASSWORD`/`MANHATTAN_SECRET` OAuth flow).
- Preloads all ASNs eligible for receiving (`AsnStatus` In Transit / In
  Receiving) into a client-side cache after auth.
- Scan/type a single ASN number — "Load ASN" enables only when it matches a
  preloaded, receivable ASN.
- Loads the ASN and displays its lines: Line #, Item (with a small item
  image thumbnail — hover to enlarge), Description, Shipped Qty, Received
  Qty, UOM.

Not yet built (next phase): clicking a line to select Partial Line / Full
Line and calling the Receive LPN API with a nextup LPN number.

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
