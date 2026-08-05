#!/usr/bin/env python3
"""Receiving Workbench — shared service for the web API."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from mawm_client import (
    asn_status_description,
    resolve_location,
    search_asn,
    search_asns,
    search_items,
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


def _asn_line_id(line: dict) -> str:
    for key in ("AsnLineId", "asnLineId", "PK", "Unique_Identifier"):
        val = line.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _received_qty_by_asn_line(asn: dict) -> Dict[str, Decimal]:
    """Sum quantity already received into LPNs, keyed by AsnLineId.

    MAWM's asn/search response doesn't expose a per-line "received" field
    directly — it's derived from the nested Lpn[].LpnDetail[] rows, each of
    which records the AsnLineId + ShippedQuantity it consumed (same
    derivation supplierenablement uses for "already cartonized" quantity).
    """
    totals: Dict[str, Decimal] = {}
    for lpn in asn.get("Lpn") or []:
        for detail in lpn.get("LpnDetail") or []:
            asn_line_id = str(detail.get("AsnLineId") or "").strip()
            if not asn_line_id:
                continue
            totals[asn_line_id] = totals.get(asn_line_id, Decimal("0")) + _dec(
                detail.get("ShippedQuantity")
            )
    return totals


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
    received_by_line = _received_qty_by_asn_line(asn)

    lines: List[dict] = []
    for idx, line in enumerate(raw_lines, start=1):
        item_id = str(line.get("ItemId") or "")
        if not item_id:
            continue
        asn_line_id = _asn_line_id(line)
        item = items.get(item_id) or {}
        shipped_base = _dec(line.get("ShippedQuantity"))
        received_base = received_by_line.get(asn_line_id, Decimal("0"))
        factor, display_uom = _package_conversion_factor(
            item, line.get("QuantityUomId")
        )
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
                "shippedQuantity": _num(shipped_base / factor),
                "receivedQuantity": _num(received_base / factor),
                "quantityUomId": display_uom,
            }
        )

    return {
        "success": True,
        "asnId": asn_id,
        "facility": dest,
        "asnStatus": asn.get("AsnStatus"),
        "asnStatusLabel": asn_status_description(asn.get("AsnStatus")),
        "vendorId": asn.get("VendorId"),
        "lineCount": len(lines),
        "lines": lines,
    }
