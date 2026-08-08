#!/usr/bin/env python3
"""Shared MAWM API client for Receiving Workbench."""

import os
import re
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

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
ILPN_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/ilpn/search"
CONTAINER_CONDITION_SEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/containerCondition/search"
LOCATION_QUICKSEARCH_URL = f"{HOST}/dcinventory/api/dcinventory/location/quickSearch"
TRANSACTION_SEARCH_URL = f"{HOST}/receiving/api/task/transaction/search"

# CONFIRMED live (2026-08-08 HAR capture of a real mobile RF Receiving
# session, missingdimensions.har) — the DMM Mobile Facade "Receiving"
# workflow, structurally identical to taskcompletion's Putaway flow.
# This is what real RF Receiving actually uses, and the only confirmed
# way to clear a receiving WARNING (receive_lpn()'s core-API endpoint
# detects the same warning but has no known override for it). Full
# confirmed sequence — every step resubmits the ENTIRE workflowVO from
# the previous response, mutating only the one field noted:
#   1. workflow_init("Receiving", "Receive") -> currentState="AcceptASN".
#      ("Receiving" here is the selected TransactionId, not a fixed
#      literal — the HAR happened to use this app's own default.)
#   2. state.ASNId = <asn id>, execute AcceptASN -> "AcceptStagingLocation".
#   3. state.EnteredLocation = <staging location>, execute
#      AcceptStagingLocation -> "AcceptLPN". UNCONFIRMED whether this
#      can be blank — the capture always had a real staging location.
#   4. state.LPNId = "" (blank — CONFIRMED this auto-assigns a fresh
#      iLPN server-side, e.g. "LPN09159"; do not pre-generate one for
#      this path), execute AcceptLPN -> "AcceptItem".
#   5. state.itemBarcode = <item id>, execute AcceptItem. This is where
#      RCV::995 ("Item Missing Critical Dims") fired: 400,
#      state.errorVOList = [{errorCode: "RCV::995", errorCategory:
#      "WARNING", errorMessage: "...", componentName:
#      "com-manh-cp-receiving"}], state.warningOverrideList = [] going in.
#   6. Override, CONFIRMED live: resubmit the same AcceptItem action
#      with state.warningOverrideList = ["RCV::995"] added (errorVOList
#      left as-is from the error response) -> 200, errorVOList cleared,
#      currentState="AcceptQuantity". Same mechanism as Putaway's
#      apply_warning_overrides() below — no receiving-specific change
#      to that function needed.
#   7. state.EnteredQuantity.scannedQuantity1 = <quantity>, execute
#      AcceptQuantity -> currentState loops back to "AcceptLPN" (ready
#      for the next item/LPN). UNCONFIRMED whether this wants base or
#      display-UOM quantity — the only captured example was a UNIT-uom
#      item (factor 1, indistinguishable).
# See CLAUDE.md's "Warning message handling" section and
# rw_service.receive_via_dmm_workflow() for the orchestration.
DMM_WORKFLOW_INIT_URL = f"{HOST}/dmmobile-facade/api/dmmobile-facade/workflow/init"
DMM_WORKFLOW_EXECUTE_URL_TEMPLATE = (
    f"{HOST}/dmmobile-facade/services/rest/workflow/execute/"
    "workflowScriptName/{script}/stateName/{state}/actionName/{action}"
)
RECEIVING_WORKFLOW_SCRIPT_NAME = "Receiving"

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

# LpnStatus (receiving component, i.e. ASN's own nested Lpn[].LpnStatus) —
# mawm_api_library/_conventions/statuses.md. Labels confirmed verbatim
# against a live LPN Inquiry response's own LpnStatusDescription field
# (e.g. "4000" -> "Received").
LPN_STATUS_LABELS = {
    "1000": "In Transit",
    "3000": "In Receiving",
    "4000": "Received",
    "7000": "Not Received ASN Verified",
    "9000": "Canceled",
}


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


def lpn_status_description(status_id) -> str:
    """Human LPN status only, e.g. 'Received'."""
    if status_id in (None, ""):
        return ""
    key = str(status_id).strip()
    return LPN_STATUS_LABELS.get(key) or key


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
    staging_location_id: str = None,
) -> dict:
    """POST receiving/lpn/receive — books received quantity against a new LPN.

    `line_items` is [{"ItemId": ..., "Quantity": ...}, ...]; Quantity is in
    the item's BASE unit (matching AsnLine.ShippedQuantity), not the item's
    display/pack UOM — confirmed directly: a live Quantity=1 receive against
    a PACK-uom item (10 base units per pack) produced OnHand=1 (one base
    unit) and the ASN line's received quantity read back as 0.1 packs, not
    1 pack. rw_service.receive_line() converts the display-UOM quantity the
    user enters into base units before calling this.

    transaction_id/receiving_strategy come from the user's Transaction ID
    picker (search_receiving_transactions) — each TransactionId there
    carries its own paired StrategyId, so receiving_strategy is derived
    from whichever TransactionId was picked, not chosen independently.
    The defaults here ("Receiving" / "Receiving Strategy") only apply if
    a caller doesn't supply them.

    This endpoint can surface a WARNING (see extract_warning()) — e.g.
    `RCV::995` when an item is missing critical dimensions — but has NO
    known override contract of its own: taskcompletion confirmed a
    guessed `userInputs: {code: code}` shape does NOT clear a warning on
    a similar core endpoint (`putaway/api/putaway/execution/container
    /move`), and this app's own HAR capture (2026-08-08,
    `missingdimensions.har`) confirmed real RF Receiving doesn't even
    use this endpoint for a warning-clearing retry — it drives the DMM
    Mobile Facade "Receiving" workflow instead. See
    rw_service.receive_via_dmm_workflow() for the confirmed-working
    override path; this function has no `warning_overrides` parameter
    on purpose, to avoid implying one exists here.
    """
    token = normalize_token(token)
    payload = {
        "AsnId": asn_id,
        "LpnId": lpn_id,
        "TransactionId": transaction_id,
        "ReceivingStrategy": receiving_strategy,
        "UserInputLineItemList": line_items,
    }
    if staging_location_id:
        payload["LocationId"] = staging_location_id
    response = _post(
        LPN_RECEIVE_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"lpn/receive failed: {response.status_code} {response.text[:800]}"
        )
    if not isinstance(body, dict):
        body = {"data": body}
    body["_requestPayload"] = payload
    body["_httpOk"] = response.status_code in (200, 201)
    return body


def extract_warning(body) -> Optional[Dict[str, str]]:
    """Best-effort scan for an overrideable WARNING in a MAWM response.

    Checks both shapes this app actually hits: the standard
    `messages.Message[]` envelope (`Type=="WARNING"`, from the core
    `receiving/lpn/receive` endpoint) and the DMM Mobile Facade
    `workflowVO.header.state.errorVOList` shape (`errorCategory==
    "WARNING"`, from the Receiving workflow — see
    RECEIVING_WORKFLOW_SCRIPT_NAME's comment). Ported from
    taskcompletion/mawm_client.py's extract_warning(); both branches
    are now CONFIRMED live for receiving (2026-08-08), not just
    inherited defensively.
    """
    if not isinstance(body, dict):
        return None
    for msg in ((body.get("messages") or {}).get("Message") or []):
        if not isinstance(msg, dict):
            continue
        category = str(msg.get("Type") or msg.get("Category") or "").upper()
        if category == "WARNING":
            return {
                "code": str(msg.get("Code") or msg.get("MessageKey") or ""),
                "text": str(msg.get("Description") or msg.get("Message") or ""),
            }

    workflow_vo = body.get("workflowVO")
    if isinstance(workflow_vo, dict):
        state = ((workflow_vo.get("header") or {}).get("state") or {})
        for err in state.get("errorVOList") or []:
            if isinstance(err, dict) and str(err.get("errorCategory") or "").upper() == "WARNING":
                return {
                    "code": str(err.get("errorCode") or ""),
                    "text": str(err.get("errorMessage") or ""),
                }

    return None


def extract_message(body) -> str:
    """Best-effort human-readable message for a failed response.

    Prefers the first `messages.Message[].Description` (any Type, not
    just WARNING) over the generic top-level `message`/`messageKey`
    (often just an opaque code like `"error.400"`). Ported from
    taskcompletion/mawm_client.py's extract_message().
    """
    if not isinstance(body, dict):
        return "Receive failed"
    for msg in ((body.get("messages") or {}).get("Message") or []):
        if isinstance(msg, dict):
            text = str(msg.get("Description") or msg.get("Message") or "").strip()
            if text:
                return text
    return str(body.get("message") or body.get("messageKey") or "Receive failed")


def workflow_init(
    transaction_id: str, transaction_type: str, token: str, org: str, location: str = None
) -> dict:
    """CONFIRMED live — bootstrap a fresh DMM Mobile Facade workflow
    session (see RECEIVING_WORKFLOW_SCRIPT_NAME's comment for the full
    confirmed Receiving sequence). Body is a literal `{}`; everything
    is driven by the query params. Returns the initial
    `{"workflowVO": {...}}`. Ported from taskcompletion/mawm_client.py's
    workflow_init(), which is domain-agnostic despite living in a
    Putaway-focused file.
    """
    token = normalize_token(token)
    url = (
        f"{DMM_WORKFLOW_INIT_URL}"
        f"?transactionId={quote(transaction_id)}&transactionType={quote(transaction_type)}"
    )
    response = _post(url, headers=build_receiving_headers(token, org, location=location), json={})
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"workflow init failed: {response.status_code} {response.text[:800]}"
        )
    return body


def workflow_execute(
    script_name: str,
    state_name: str,
    action_name: str,
    workflow_vo: dict,
    token: str,
    org: str,
    location: str = None,
) -> dict:
    """CONFIRMED live — resubmit a DMM Mobile Facade workflow action.
    `workflow_vo` must be the complete object from the immediately
    preceding call (init or execute) — every other field it carries is
    required as-is. The scanned/entered input for this action (e.g.
    `ASNId`, `EnteredLocation`, `LPNId`, `itemBarcode`,
    `EnteredQuantity.scannedQuantity1`) lives *inside*
    `workflow_vo["header"]["state"]` — the caller must set it there
    before calling this, the same way `apply_warning_overrides()`
    mutates `header.state.warningOverrideList`. Ported from
    taskcompletion/mawm_client.py's workflow_execute() (there it warns
    a stray top-level sibling field silently produces a generic
    `serverError` for Putaway — same caution applies here).
    """
    token = normalize_token(token)
    url = DMM_WORKFLOW_EXECUTE_URL_TEMPLATE.format(
        script=script_name, state=state_name, action=action_name
    )
    payload = {"workflowVO": workflow_vo}
    response = _post(url, headers=build_receiving_headers(token, org, location=location), json=payload)
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:1200]}
    if not isinstance(body, dict):
        body = {"data": body}
    if response.status_code not in (200, 201) and "raw" in body and len(body) == 1:
        raise RuntimeError(
            f"workflow execute ({action_name}) failed: {response.status_code} {response.text[:800]}"
        )
    return body


def apply_warning_overrides(workflow_vo: dict, warning_overrides: Optional[Dict[str, str]]) -> dict:
    """Mutates (and returns) `workflow_vo` so its
    `header.state.warningOverrideList` includes every code in
    `warning_overrides` — the CONFIRMED live mechanism for clearing a
    DMM Mobile Facade warning (see RECEIVING_WORKFLOW_SCRIPT_NAME's
    comment). No-op if there's nothing to apply. Ported verbatim from
    taskcompletion/mawm_client.py's apply_warning_overrides().
    """
    if not warning_overrides or not isinstance(workflow_vo, dict):
        return workflow_vo
    state = (workflow_vo.get("header") or {}).get("state")
    if not isinstance(state, dict):
        return workflow_vo
    existing = list(state.get("warningOverrideList") or [])
    for code in warning_overrides:
        if code not in existing:
            existing.append(code)
    state["warningOverrideList"] = existing
    return workflow_vo


def search_inventory_by_container_item(
    container_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, Dict[str, Decimal]]:
    """{LpnId: {ItemId: OnHand}} via dcinventory's Inventory object.

    This is the ground truth for received quantity — confirmed directly
    against a live receive: a single lpn/receive call with Quantity=2
    left the ASN's own Lpn[].LpnDetail[].ReportingUomQuantity at 1 (that
    field does not reliably reflect quantities above 1), while this same
    LPN's Inventory record correctly showed OnHand=2. Use this, not
    LpnDetail, whenever the real received quantity matters. Broken out
    per-item (not just summed per LPN) so a multi-item LPN can be detected.
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
    out: Dict[str, Dict[str, Decimal]] = {}
    for row in _response_data_list(response.json()):
        cid = str(row.get("InventoryContainerId") or "").strip()
        item_id = str(row.get("ItemId") or "").strip()
        if not cid or not item_id:
            continue
        onhand = Decimal(str(row.get("OnHand") or 0))
        bucket = out.setdefault(cid, {})
        bucket[item_id] = bucket.get(item_id, Decimal("0")) + onhand
    return out


def search_ilpn_locations_by_asn(
    asn_id: str, token: str, org: str, location: str = None
) -> Dict[str, str]:
    """{LpnId: CurrentLocationId} for every ILPN linked to this ASN.

    Verified directly against a live "LPN Inquiry" response: this
    matches its LocationId field exactly.
    """
    token = normalize_token(token)
    payload = {
        "Query": f"AsnId ='{asn_id}'",
        "Size": 200,
        "Page": 0,
        "Template": {"IlpnId": "", "CurrentLocationId": ""},
    }
    response = _post(
        ILPN_SEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"ilpn search failed: {response.status_code} {response.text[:500]}"
        )
    out: Dict[str, str] = {}
    for row in _response_data_list(response.json()):
        lpn_id = str(row.get("IlpnId") or "").strip()
        if lpn_id:
            out[lpn_id] = str(row.get("CurrentLocationId") or "").strip()
    return out


def search_container_conditions(
    container_ids: List[str], token: str, org: str, location: str = None
) -> Dict[str, List[str]]:
    """{LpnId: [ConditionCode, ...]} via dcinventory's containerCondition object.

    Not exposed on ilpn/search or inventory/search — this dedicated object
    is the only place it was found to be queryable (confirmed directly:
    it matched the ConditionCodes the lpn/receive response itself echoed
    back at receive time).
    """
    clean = sorted({str(c).strip() for c in container_ids if str(c).strip()})
    if not clean:
        return {}
    token = normalize_token(token)
    quoted = ", ".join(f"'{c}'" for c in clean)
    payload = {
        "Query": f"InventoryContainerId in ({quoted})",
        "Size": max(len(clean) * 3, 50),
        "Page": 0,
    }
    response = _post(
        CONTAINER_CONDITION_SEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"containerCondition search failed: {response.status_code} {response.text[:500]}"
        )
    out: Dict[str, List[str]] = {}
    for row in _response_data_list(response.json()):
        cid = str(row.get("InventoryContainerId") or "").strip()
        code = str(row.get("ConditionCode") or "").strip()
        if not cid or not code:
            continue
        bucket = out.setdefault(cid, [])
        if code not in bucket:
            bucket.append(code)
    return out


def search_staging_locations(token: str, org: str, location: str = None) -> List[dict]:
    """All active STAGING locations, for the receiving staging-location picker."""
    token = normalize_token(token)
    payload = {
        "Query": "IsActive=true and LocationTypeId='STAGING'",
        "Template": {"LocationId": "", "DisplayLocation": ""},
        "Size": 1000,
        "Sort": [{"attribute": "LocationId", "direction": "ASC"}],
    }
    response = _post(
        LOCATION_QUICKSEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"location quickSearch failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())


def search_receiving_transactions(token: str, org: str, location: str = None) -> List[dict]:
    """All receiving TransactionIds for this org, for the Transaction ID picker.

    Each row also carries its own StrategyId (e.g. "Receiving" pairs with
    "Receiving Strategy") — that's the ReceivingStrategy to send alongside
    whichever TransactionId is picked, not a separately hardcoded value.
    """
    token = normalize_token(token)
    payload = {
        "Query": "",
        "Size": 1000,
        "Sort": {"attribute": "TransactionId", "direction": "asc"},
    }
    response = _post(
        TRANSACTION_SEARCH_URL,
        headers=build_receiving_headers(token, org, location=location),
        json=payload,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"transaction search failed: {response.status_code} {response.text[:500]}"
        )
    return _response_data_list(response.json())
