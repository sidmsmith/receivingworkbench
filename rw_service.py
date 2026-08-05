#!/usr/bin/env python3
"""Receiving Workbench — shared service for the web API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from mawm_client import (
    asn_status_description,
    generate_ilpn_ids,
    lpn_status_description,
    qty_for_payload,
    receive_lpn,
    resolve_location,
    search_asn,
    search_asns,
    search_container_conditions,
    search_ilpn_locations_by_asn,
    search_inventory_by_container_item,
    search_items,
    search_staging_locations,
)


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _num(value):
    d = _dec(value)
    if d == d.to_integral_value():
        return int(d)
    return float(d)


def preload_asn_index(
    token: str,
    org: str,
    location: str = None,
) -> Dict[str, Any]:
    """Preload all ASNs eligible for receiving (In Transit / In Receiving).

    Returns a light index the frontend caches client-side to validate a
    scanned ASN id instantly, without a round trip per scan.
    """
    dest = resolve_location(org, location)
    rows = search_asns(token, org, location=dest)
    entries = []
    for row in rows:
        asn_id = str(row.get("AsnId") or "").strip()
        if not asn_id:
            continue
        entries.append(
            {
                "asnId": asn_id,
                "status": str(row.get("AsnStatus") or ""),
                "statusLabel": asn_status_description(row.get("AsnStatus")),
                "vendorId": str(row.get("VendorId") or ""),
                "destinationFacilityId": str(row.get("DestinationFacilityId") or ""),
            }
        )
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
    }


def preload_staging_locations(
    token: str,
    org: str,
    location: str = None,
) -> Dict[str, Any]:
    """All active STAGING locations, for the staging-location picker."""
    dest = resolve_location(org, location)
    rows = search_staging_locations(token, org, location=dest)
    entries = []
    for row in rows:
        location_id = str(row.get("LocationId") or "").strip()
        if not location_id:
            continue
        entries.append(
            {
                "locationId": location_id,
                "displayLocation": str(row.get("DisplayLocation") or "").strip() or location_id,
            }
        )
    # The MAWM query itself sorts by LocationId (asked for explicitly); the
    # picker should read by DisplayLocation instead, so re-sort here.
    entries.sort(key=lambda e: e["displayLocation"].lower())
    return {
        "success": True,
        "count": len(entries),
        "entries": entries,
    }


def _package_conversion_factor(item: dict, quantity_uom_id: str):
    """Find the item's standard ItemPackage entry for this UOM code.

    MAWM's AsnLine.ShippedQuantity is always expressed in the item's base
    unit; QuantityUomId is the *code* the line was shipped in (UNIT, PACK,
    BUNDLE, LPN, PALLET, or a custom code). The item's ItemPackage[] array
    holds the actual conversion factor (Quantity) and human label (UomId)
    for that code — e.g. StandardQuantityUomId="LPN" -> UomId="Case",
    Quantity=40 means 40 base units per case. Falls back to (1, raw code)
    when the item has no matching standard package entry (e.g. an item
    whose only unit *is* the base unit).
    """
    code = str(quantity_uom_id or "").strip()
    for pkg in item.get("ItemPackage") or []:
        if pkg.get("Standard") is not True:
            continue
        if str(pkg.get("StandardQuantityUomId") or "").strip() != code:
            continue
        factor = _dec(pkg.get("Quantity"))
        if factor > 0:
            return factor, str(pkg.get("UomId") or code)
    return Decimal("1"), code or "UNIT"


def _unit_label(item: dict) -> str:
    """The item's base-unit ItemPackage label (e.g. "units"), for the
    remainder portion of a mixed-UOM display. Falls back to "units"."""
    for pkg in item.get("ItemPackage") or []:
        if pkg.get("Standard") is True and str(pkg.get("StandardQuantityUomId") or "") == "UNIT":
            label = str(pkg.get("UomId") or "").strip()
            if label:
                return label
    return "units"


def _format_mixed_qty(base_qty: Decimal, factor: Decimal, pack_uom: str, unit_uom: str) -> str:
    """Format a base-unit quantity the same way MAWM's RF receiving UI does.

    Verified directly against a live RF Receiving session capture: a
    quantity that doesn't divide evenly into the item's pack UOM is shown
    as "{whole packs} {packUom} {remainder} {unit label}" (e.g.
    "0 packs 1 units" for 1 base unit of a 10-per-pack item) — never as a
    decimal pack count. When it divides evenly, only the pack part is
    shown ("3 Case", no "0 units" suffix). Zero quantity renders as "" —
    RF's own "Received quantity" column is blank until something has
    actually been received, not "0 <uom>".
    """
    if base_qty <= 0:
        return ""
    if factor <= 0 or factor == 1:
        return f"{_num(base_qty)} {pack_uom}"
    quotient = int(base_qty // factor)
    remainder = base_qty - (Decimal(quotient) * factor)
    if remainder == 0:
        return f"{quotient} {pack_uom}"
    return f"{quotient} {pack_uom} {_num(remainder)} {unit_uom}"


def _asn_line_id(line: dict) -> str:
    for key in ("AsnLineId", "asnLineId", "PK", "Unique_Identifier"):
        val = line.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _received_qty_by_asn_line(
    asn: dict, token: str, org: str, location: str = None
) -> Dict[str, Decimal]:
    """Sum quantity already received into LPNs, keyed by AsnLineId.

    MAWM's asn/search response doesn't expose a reliable per-line "received"
    field directly. Two things were tried and rejected before this:

    1. Lpn[].LpnDetail[].ShippedQuantity — always 0 for a receive-created
       LPN (that's supplierenablement's *different* lpn/create flow).
    2. Lpn[].LpnDetail[].ReportingUomQuantity — looked right for
       single-unit receives, but confirmed wrong for a multi-unit one: a
       single lpn/receive call with Quantity=2 left ReportingUomQuantity
       at 1 on the resulting LpnDetail (verified: MAWM's own dmui-facade
       ASN-line report showed the true total was 1 higher than summing
       this field would give).

    The AsnLineId link on Lpn[].LpnDetail[] *is* reliable, so this uses it
    only to map each LPN to its line, then sums the real quantity from
    dcinventory's Inventory object (OnHand) per LPN — confirmed to match
    MAWM's own dmui-facade ASN-line report (ReceivedQuantity) exactly
    across every UOM on the test ASN (UNIT, PACK, BUNDLE, LPN/Case,
    PALLET).
    """
    lpn_to_line: Dict[str, str] = {}
    for lpn in asn.get("Lpn") or []:
        lpn_id = str(lpn.get("LpnId") or "").strip()
        if not lpn_id:
            continue
        for detail in lpn.get("LpnDetail") or []:
            asn_line_id = str(detail.get("AsnLineId") or "").strip()
            if asn_line_id:
                lpn_to_line[lpn_id] = asn_line_id
                break
    if not lpn_to_line:
        return {}

    onhand_by_lpn = search_inventory_by_container_item(
        list(lpn_to_line.keys()), token, org, location=location
    )
    totals: Dict[str, Decimal] = {}
    for lpn_id, asn_line_id in lpn_to_line.items():
        qty = sum(onhand_by_lpn.get(lpn_id, {}).values(), Decimal("0"))
        totals[asn_line_id] = totals.get(asn_line_id, Decimal("0")) + qty
    return totals


def _build_lpn_summaries(
    asn: dict, asn_id: str, items: Dict[str, dict], token: str, org: str, location: str
) -> List[dict]:
    """One row per LPN linked to this ASN, for the LPN panel.

    Quantity comes from dcinventory's Inventory object (OnHand), same as
    _received_qty_by_asn_line — LpnDetail's own quantity fields are not
    reliable (see that function's docstring). Location and status come
    from the ASN's own nested Lpn[] plus one AsnId-scoped ilpn/search
    call; condition codes need a dedicated containerCondition/search
    call, since neither ilpn/search nor inventory/search expose them.
    """
    lpn_status: Dict[str, str] = {}
    lpn_item_ids: Dict[str, List[str]] = {}
    for lpn in asn.get("Lpn") or []:
        lpn_id = str(lpn.get("LpnId") or "").strip()
        if not lpn_id:
            continue
        lpn_status[lpn_id] = str(lpn.get("LpnStatus") or "")
        seen: List[str] = []
        for detail in lpn.get("LpnDetail") or []:
            item_id = str(detail.get("ItemId") or "").strip()
            if item_id and item_id not in seen:
                seen.append(item_id)
        lpn_item_ids[lpn_id] = seen

    lpn_ids = list(lpn_item_ids.keys())
    if not lpn_ids:
        return []

    locations = search_ilpn_locations_by_asn(asn_id, token, org, location=location)
    onhand = search_inventory_by_container_item(lpn_ids, token, org, location=location)
    conditions = search_container_conditions(lpn_ids, token, org, location=location)

    all_item_ids = {i for ids in lpn_item_ids.values() for i in ids}
    missing_item_ids = all_item_ids - set(items.keys())
    if missing_item_ids:
        items = dict(items)
        items.update(search_items(list(missing_item_ids), token, org, location=location))

    item_quantity_uom: Dict[str, str] = {}
    for line in asn.get("AsnLine") or []:
        iid = str(line.get("ItemId") or "").strip()
        if iid and iid not in item_quantity_uom:
            item_quantity_uom[iid] = line.get("QuantityUomId")

    summaries: List[dict] = []
    for lpn_id in lpn_ids:
        item_ids = lpn_item_ids.get(lpn_id) or []
        item_onhand = onhand.get(lpn_id, {})
        present_items = [i for i in item_ids if item_onhand.get(i, Decimal("0")) > 0] or item_ids
        mixed = len(present_items) > 1

        item_display = ""
        description = ""
        qty_display: Any = ""
        uom_display = ""
        if mixed:
            item_display = "MIXED"
        elif present_items:
            item_id = present_items[0]
            item = items.get(item_id) or {}
            item_display = item_id
            description = item.get("Description") or item.get("ItemDescription") or ""
            base_qty = item_onhand.get(item_id, Decimal("0"))
            factor, display_uom = _package_conversion_factor(
                item, item_quantity_uom.get(item_id)
            )
            qty_display = _num(base_qty / factor)
            uom_display = display_uom

        summaries.append(
            {
                "lpnId": lpn_id,
                "statusLabel": lpn_status_description(lpn_status.get(lpn_id)),
                "location": locations.get(lpn_id) or "",
                "itemId": item_display,
                "description": description,
                "qty": qty_display,
                "uom": uom_display,
                "conditionCode": ", ".join(conditions.get(lpn_id) or []),
            }
        )

    summaries.sort(key=lambda r: r["lpnId"])
    return summaries


def load_asn_for_receiving(
    token: str,
    org: str,
    asn_id: str,
    location: str = None,
) -> Dict[str, Any]:
    if not asn_id:
        return {"success": False, "error": "AsnId required"}
    dest = resolve_location(org, location)
    asn = search_asn(asn_id, token, org, location=dest)
    if not asn:
        return {"success": False, "error": f"ASN {asn_id} not found"}

    raw_lines = asn.get("AsnLine") or []
    if not isinstance(raw_lines, list):
        raw_lines = []

    item_ids = [str(l.get("ItemId") or "") for l in raw_lines if l.get("ItemId")]
    items = search_items(item_ids, token, org, location=dest) if item_ids else {}
    received_by_line = _received_qty_by_asn_line(asn, token, org, location=dest)

    lines: List[dict] = []
    for idx, line in enumerate(raw_lines, start=1):
        item_id = str(line.get("ItemId") or "")
        if not item_id:
            continue
        asn_line_id = _asn_line_id(line)
        item = items.get(item_id) or {}
        shipped_base = _dec(line.get("ShippedQuantity"))
        received_base = received_by_line.get(asn_line_id, Decimal("0"))
        remaining_base = shipped_base - received_base
        factor, display_uom = _package_conversion_factor(
            item, line.get("QuantityUomId")
        )
        unit_label = _unit_label(item)
        lines.append(
            {
                "lineNumber": idx,
                "asnLineId": asn_line_id,
                "itemId": item_id,
                "description": item.get("Description")
                or item.get("ItemDescription")
                or line.get("Description")
                or "",
                "itemImageUrl": item.get("ImageUrl")
                or item.get("imageUrl")
                or item.get("ImageURL")
                or "",
                # Decimal pack-uom quantities — used for remaining/qty math
                # (Partial Line default+cap), not for display.
                "shippedQuantity": _num(shipped_base / factor),
                "receivedQuantity": _num(received_base / factor),
                "quantityUomId": display_uom,
                # MAWM RF-style mixed "{packs} {uom} {remainder} {unit}"
                # display strings — what the table actually renders.
                "shippedQuantityLabel": _format_mixed_qty(
                    shipped_base, factor, display_uom, unit_label
                ),
                "receivedQuantityLabel": _format_mixed_qty(
                    received_base, factor, display_uom, unit_label
                ),
                "remainingQuantityLabel": _format_mixed_qty(
                    remaining_base, factor, display_uom, unit_label
                ),
            }
        )

    lpns = _build_lpn_summaries(asn, asn_id, items, token, org, dest)

    return {
        "success": True,
        "asnId": asn_id,
        "facility": dest,
        "asnStatus": asn.get("AsnStatus"),
        "asnStatusLabel": asn_status_description(asn.get("AsnStatus")),
        "vendorId": asn.get("VendorId"),
        "lineCount": len(lines),
        "lines": lines,
        "lpnCount": len(lpns),
        "lpns": lpns,
    }


def _line_state_for_receipt(
    token: str, org: str, asn_id: str, asn_line_id: str, location: str = None
) -> Dict[str, Any]:
    """Re-fetch the ASN fresh and resolve one line's authoritative base-unit state.

    Re-fetching (rather than trusting quantities the frontend already has)
    avoids acting on stale data — another receive could have happened since
    the table was last loaded.

    POSSIBLE GAP, not confirmed against this corrected read path: an
    earlier test against the previous (since-replaced) received-quantity
    source suggested a read immediately after a write could lag by about
    one receive's worth — but that source was later found to be wrong in
    a way that fully explains what looked like lag (see
    _received_qty_by_asn_line's docstring), so it's unclear how much of
    that observation was real eventual consistency vs. just the bug. Not
    re-verified against dcinventory's Inventory search specifically. If
    two receive calls land on the *same* line back-to-back faster than
    any real lag resolves, "remaining" could still be computed from a
    stale read. Not mitigated (no poll-until-visible/retry-with-reverify
    added). Low risk today since the busy-overlay blocks a rapid
    double-click on the
    same line, but worth a real test (mirroring supplierenablement's
    poll-until-visible pattern, mawm_api_library/patterns/technical/
    paginated-fetch-all.md's related note, or dispatch_request's
    retry-fallback-verification pattern) before this sees real usage.
    """
    dest = resolve_location(org, location)
    asn = search_asn(asn_id, token, org, location=dest)
    if not asn:
        return {"success": False, "error": f"ASN {asn_id} not found"}

    raw_lines = asn.get("AsnLine") or []
    line = next(
        (l for l in raw_lines if _asn_line_id(l) == str(asn_line_id)), None
    )
    if not line:
        return {"success": False, "error": f"ASN line {asn_line_id} not found on {asn_id}"}

    item_id = str(line.get("ItemId") or "")
    items = search_items([item_id], token, org, location=dest) if item_id else {}
    item = items.get(item_id) or {}

    shipped_base = _dec(line.get("ShippedQuantity"))
    received_base = _received_qty_by_asn_line(asn, token, org, location=dest).get(
        asn_line_id, Decimal("0")
    )
    remaining_base = shipped_base - received_base
    factor, display_uom = _package_conversion_factor(item, line.get("QuantityUomId"))

    return {
        "success": True,
        "dest": dest,
        "itemId": item_id,
        "shippedBase": shipped_base,
        "receivedBase": received_base,
        "remainingBase": remaining_base,
        "factor": factor,
        "displayUom": display_uom,
    }


def receive_line(
    token: str,
    org: str,
    asn_id: str,
    asn_line_id: str,
    mode: str,
    quantity_display: Optional[float] = None,
    location: str = None,
    staging_location_id: str = None,
) -> Dict[str, Any]:
    """Receive against one ASN line — mode "full" or "partial".

    "full" books the entire remaining quantity. "partial" books
    `quantity_display` (in the item's display/pack UOM, same units the
    table shows) after converting to base units and validating it does
    not exceed what's remaining. `staging_location_id` is optional — the
    frontend only passes one once it's resolved a typed/picked value
    against the preloaded STAGING location list, so it's trusted here.
    """
    state = _line_state_for_receipt(token, org, asn_id, asn_line_id, location=location)
    if not state.get("success"):
        return state

    remaining_base = state["remainingBase"]
    factor = state["factor"]
    if remaining_base <= 0:
        return {"success": False, "error": "No remaining quantity on this line"}

    if mode == "full":
        qty_base = remaining_base
    elif mode == "partial":
        if quantity_display is None:
            return {"success": False, "error": "quantity is required for a partial receipt"}
        qty_display = _dec(quantity_display)
        if qty_display <= 0:
            return {"success": False, "error": "quantity must be greater than 0"}
        qty_base = qty_display * factor
        if qty_base > remaining_base:
            return {
                "success": False,
                "error": f"quantity exceeds remaining ({_num(remaining_base / factor)} {state['displayUom']})",
            }
    else:
        return {"success": False, "error": f"Unknown mode: {mode}"}

    lpn_ids = generate_ilpn_ids(1, token, org, location=state["dest"])
    lpn_id = lpn_ids[0]

    # TODO(testing): large qty_base may trip the item's MaxLpnQuantity on
    # MAWM's side (warning or error) — not handled yet, see receive_lpn().
    result = receive_lpn(
        asn_id,
        lpn_id,
        [{"ItemId": state["itemId"], "Quantity": qty_for_payload(qty_base)}],
        token,
        org,
        location=state["dest"],
        staging_location_id=staging_location_id or None,
    )
    ok = result.get("success", True) if isinstance(result, dict) else True
    return {
        "success": bool(ok),
        "lpnId": lpn_id,
        "itemId": state["itemId"],
        "quantityBase": _num(qty_base),
        "quantityDisplay": _num(qty_base / factor),
        "displayUom": state["displayUom"],
        "mawmResponse": result,
        "error": None if ok else (result.get("message") or result.get("error") or "Receive failed"),
    }
