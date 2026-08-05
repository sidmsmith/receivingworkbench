#!/usr/bin/env python3
"""Shared MAWM API client for Receiving Workbench."""

import os
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import requests
import urllib3
from requests.auth import HTTPBasicAuth

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = "https://salep.sce.manh.com"
AUTH_HOST = os.getenv("MANHATTAN_AUTH_HOST", "salep-auth.sce.manh.com")

ASN_SEARCH_URL = f"{HOST}/receiving/api/receiving/asn/search"
ITEM_SEARCH_URL = f"{HOST}/item-master/api/item-master/item/search"
LPN_RECEIVE_URL = f"{HOST}/receiving/api/receiving/lpn/receive"
ILPN_GENERATE_URL = f"{HOST}/dcinventory/api/dcinventory/ilpn/generateIlpnIds"
INVENTORY_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/inventory/search"

USERNAME_BASE = os.getenv("MANHATTAN_USERNAME_BASE", "sdtadmin@")
CLIENT_ID = os.getenv("MANHATTAN_CLIENT_ID", "omnicomponent.1.0.0")
REQUEST_TIMEOUT = 60

_session = requests.Session()
_session.trust_env = False
_NO_PROXY = {"http": None, "https": None}

# AsnStatus — mawm_api_library/_conventions/statuses.md#asn
ASN_STATUS_LABELS = {
    "0000": "Planning",
    "0500": "Open",
    "1000": "In Transit",
    "3000": "In Receiving",
    "8000": "Verified",
    "9000": "Canceled",
}

# Statuses eligible for receiving — confirmed with the user.
RECEIVABLE_ASN_STATUSES = ("1000", "3000")


def _get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
    kwargs.setdefault("proxies", _NO_PROXY)
    return _session.get(url, **kwargs)


def _post(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
    kwargs.setdefault("proxies", _NO_PROXY)
    return _session.post(url, **kwargs)


def normalize_token(token: str) -> str:
    """Clean pasted tokens: strip whitespace, quotes, and redundant Bearer prefix."""
    token = (token or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        token = token[1:-1].strip()
    return token


def resolve_location(org: str, location: str = None, default_suffix: str = "DM1") -> str:
    """Resolve full facility id for selectedLocation."""
    org = org.upper()
    if location and str(location).strip():
        loc = str(location).strip().upper()
        if loc.startswith(org):
            return loc
        if "-" in loc:
            return loc
        return f"{org}-{loc}"
    return f"{org}-{default_suffix}"


def build_receiving_headers(
    token: str, org: str, facility_suffix: str = "DM1", location: str = None
) -> dict:
    org = org.upper()
    loc = resolve_location(org, location, facility_suffix)
    token = normalize_token(token)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "selectedOrganization": org,
        "selectedLocation": loc,
    }


def get_manhattan_token(org: str) -> Optional[str]:
    """Obtain OAuth token using MANHATTAN_PASSWORD and MANHATTAN_SECRET env vars."""
    password = os.getenv("MANHATTAN_PASSWORD", "").strip()
    secret = os.getenv("MANHATTAN_SECRET", "").strip()
    if not password or not secret:
        return None

    url = f"https://{AUTH_HOST}/oauth/token"
    username = f"{USERNAME_BASE}{org.lower()}"
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    auth = HTTPBasicAuth(CLIENT_ID, secret)
    try:
        response = _post(url, data=data, headers=headers, auth=auth)
        if response.status_code == 200:
            return response.json().get("access_token")
        print(f"OAuth failed ({response.status_code}): {response.text[:300]}")
    except requests.RequestException as exc:
        print(f"OAuth error: {exc}")
    return None


def validate_org(org: str) -> bool:
    return bool(re.match(r"^[A-Z0-9]+-DEMO$", org or ""))


def _response_data_list(body) -> List[dict]:
    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []
    data = body.get("data") or body.get("Data") or []
    return data if isinstance(data, list) else []


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def asn_status_description(status_id) -> str:
    """Human ASN status only, e.g. 'In Transit'."""
    if status_id in (None, ""):
        return ""
    key = str(status_id).strip()
    return ASN_STATUS_LABELS.get(key) or key


def search_asns(
    token: str,
    org: str,
    location: str = None,
    statuses: Tuple[str, ...] = RECEIVABLE_ASN_STATUSES,
    page_size: int = 100,
    max_pages: int = 50,
) -> List[dict]:
    """Paginated ASN search for the given statuses (Canceled excluded).

    Same total-count-aware / hard-page-cap shape as supplierenablement's
    preload_po_index, applied to ASN search instead of PO search.
    """
    headers = build_receiving_headers(token, org, location=location)
    quoted = ", ".join(f"'{s}'" for s in statuses)
    query = f"AsnStatus in ({quoted})"
    rows: List[dict] = []
    page = 0
    total = None

    while page < max_pages:
        payload = {
            "Query": query,
            "Page": page,
            "Size": page_size,
            "Template": {
                "AsnId": None,
                "AsnStatus": None,
                "Canceled": None,
                "VendorId": None,
                "DestinationFacilityId": None,
            },
        }
        response = _post(ASN_SEARCH_URL, headers=headers, json=payload)
        if response.status_code != 200:
            raise RuntimeError(
                f"ASN search failed ({response.status_code}): {response.text[:400]}"
            )
        body = response.json() if response.text else {}
        page_rows = _response_data_list(body)
        header = body.get("header") or body.get("Header") or {}
        if total is None:
            try:
                total = int(header.get("totalCount") or 0)
            except Exception:
                total = 0

        for row in page_rows:
            if row.get("Canceled") is True:
                continue
            rows.append(row)

        if not page_rows:
            break
        fetched = (page + 1) * page_size
        if total and fetched >= total:
            break
        if len(page_rows) < page_size:
            break
        page += 1

    return rows


def search_asn(asn_id: str, token: str, org: str, location: str = None) -> Optional[dict]:
    token = normalize_token(token)
    payload = {
        "Query": f"AsnId ='{asn_id}'",
        "Size": 5,
        "Page": 0,
    }
    response = _post(
        ASN_SEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ASN search failed: {response.status_code} {response.text[:500]}"
        )
    data = _response_data_list(response.json())
    return data[0] if data else None


def search_items(
    item_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, dict]:
    clean = [str(i).strip() for i in item_ids if str(i).strip()]
    if not clean:
        return {}
    quoted = ", ".join(
        f"'{item_id.replace(chr(39), chr(39) + chr(39))}'" for item_id in clean
    )
    payload = {
        "Query": f"ItemId in ({quoted})",
        "Page": 0,
        "Size": max(len(clean), 50),
        "Template": {
            "ItemId": "",
            "Description": "",
            "ImageUrl": "",
            "DisplayUomId": "",
            "ItemPackage": [
                {
                    "UomId": None,
                    "StandardQuantityUomId": None,
                    "Quantity": None,
                    "Standard": None,
                }
            ],
        },
    }
    headers = build_receiving_headers(token, org, location=location)
    headers["FacilityId"] = resolve_location(org, location)
    try:
        response = _post(ITEM_SEARCH_URL, headers=headers, json=payload)
    except requests.RequestException as exc:
        print(f"Warning: item search failed: {exc}")
        return {}
    if response.status_code != 200:
        print(f"Warning: item search failed: {response.status_code}")
        return {}
    data = _response_data_list(response.json())
    return {str(item.get("ItemId")): item for item in data if item.get("ItemId")}


def qty_for_payload(value) -> "int | float":
    dec = Decimal(str(value))
    if dec == dec.to_integral_value():
        return int(dec)
    return float(dec)


def generate_ilpn_ids(
    count: int, token: str, org: str, location: str = None
) -> List[str]:
    """Reserve `count` new iLPN numbers via dcinventory/ilpn/generateIlpnIds."""
    token = normalize_token(token)
    response = _post(
        ILPN_GENERATE_URL,
        headers=build_receiving_headers(token, org, location=location),
        json={"numberOfILpns": max(1, int(count))},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"generateIlpnIds failed ({response.status_code}): {response.text[:500]}"
        )
    body = response.json() if response.text else {}
    data = body.get("data") if isinstance(body, dict) else body
    ids = [str(v).strip() for v in (data or []) if str(v or "").strip()]
    if not ids:
        raise RuntimeError(f"generateIlpnIds returned no ids. Response: {response.text[:500]}")
    return ids


def receive_lpn(
    asn_id: str,
    lpn_id: str,
    line_items: List[dict],
    token: str,
    org: str,
    location: str = None,
    transaction_id: str = "Receiving",
    receiving_strategy: str = "Receiving Strategy",
) -> dict:
    """POST receiving/lpn/receive — books received quantity against a new LPN.

    `line_items` is [{"ItemId": ..., "Quantity": ...}, ...]; Quantity is in
    the item's BASE unit (matching AsnLine.ShippedQuantity), not the item's
    display/pack UOM — confirmed directly: a live Quantity=1 receive against
    a PACK-uom item (10 base units per pack) produced OnHand=1 (one base
    unit) and the ASN line's received quantity read back as 0.1 packs, not
    1 pack. rw_service.receive_line() converts the display-UOM quantity the
    user enters into base units before calling this.

    TransactionId/ReceivingStrategy are hardcoded per current product scope;
    a dropdown to choose these is planned but not yet built.

    TODO(testing): for a large Quantity, MAWM may reject/warn if it exceeds
    the item's max LPN quantity (item master). Not handled yet — needs a
    real test against an item with a MaxLpnQuantity set.
    """
    token = normalize_token(token)
    payload = {
        "AsnId": asn_id,
        "LpnId": lpn_id,
        "TransactionId": transaction_id,
        "ReceivingStrategy": receiving_strategy,
        "UserInputLineItemList": line_items,
    }
    response = _post(
        LPN_RECEIVE_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"lpn/receive failed: {response.status_code} {response.text[:800]}"
        )
    if isinstance(body, dict):
        body["_requestPayload"] = payload
    return body if isinstance(body, dict) else {"data": body, "_requestPayload": payload}


def search_inventory_onhand_by_container(
    container_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, Decimal]:
    """Sum OnHand per ILPN container id, via dcinventory's Inventory object.

    This is the ground truth for received quantity — confirmed directly
    against a live receive: a single lpn/receive call with Quantity=2
    left the ASN's own Lpn[].LpnDetail[].ReportingUomQuantity at 1 (that
    field does not reliably reflect quantities above 1), while this same
    LPN's Inventory record correctly showed OnHand=2. Use this, not
    LpnDetail, whenever the real received quantity matters.
    """
    clean = sorted({str(c).strip() for c in container_ids if str(c).strip()})
    if not clean:
        return {}
    token = normalize_token(token)
    quoted = ", ".join(f"'{c}'" for c in clean)
    payload = {
        "Query": f"InventoryContainerId in ({quoted}) and InventoryContainerTypeId ='ILPN'",
        "Size": max(len(clean), 50),
        "Page": 0,
    }
    response = _post(
        INVENTORY_SEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"inventory search failed: {response.status_code} {response.text[:500]}"
        )
    out: Dict[str, Decimal] = {}
    for row in _response_data_list(response.json()):
        cid = str(row.get("InventoryContainerId") or "").strip()
        if not cid:
            continue
        onhand = Decimal(str(row.get("OnHand") or 0))
        out[cid] = out.get(cid, Decimal("0")) + onhand
    return out
