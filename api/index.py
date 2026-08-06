# api/index.py
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mawm_client import get_manhattan_token, normalize_token, validate_org  # noqa: E402
from rw_service import (  # noqa: E402
    load_asn_for_receiving,
    preload_asn_index,
    preload_staging_locations,
    preload_transactions,
    receive_line,
)

app = Flask(__name__)

PASSWORD = os.getenv("MANHATTAN_PASSWORD")
CLIENT_SECRET = os.getenv("MANHATTAN_SECRET")
APP_NAME = "receivingworkbench-app"
APP_VERSION = "0.1.10"
DEFAULT_ORG = os.getenv("MANHATTAN_DEFAULT_ORG", "SS-DEMO").strip().upper() or "SS-DEMO"
TOKEN_FILE = ROOT / ".token"
USAGE_INGEST_URL = os.getenv("MANHATTAN_USAGE_INGEST_URL", "").strip()
USAGE_INGEST_SECRET = os.getenv("MANHATTAN_USAGE_INGEST_SECRET", "").strip()


def forward_usage_event(payload):
    if not USAGE_INGEST_URL:
        return
    headers = {"Content-Type": "application/json"}
    if USAGE_INGEST_SECRET:
        headers["Authorization"] = f"Bearer {USAGE_INGEST_SECRET}"
    try:
        requests.post(USAGE_INGEST_URL, json=payload, headers=headers, timeout=8, verify=False)
    except Exception as e:
        print(f"[usage] Forward failed: {e}")


def read_local_token_file() -> str:
    """Local-dev Bearer token from .token (gitignored). Empty on Vercel / missing file."""
    try:
        if not TOKEN_FILE.is_file():
            return ""
        return normalize_token(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[auth] Could not read .token: {e}")
        return ""


def resolve_bearer_token(org: str) -> tuple:
    """
    Resolve access token.
    Priority: project .token file > OAuth env vars.
    Returns (token, source) where source is 'token-file' | 'oauth' | None.
    """
    file_token = read_local_token_file()
    if file_token:
        return file_token, "token-file"
    oauth = get_manhattan_token(org)
    if oauth:
        return normalize_token(oauth), "oauth"
    return None, None


def _json():
    return request.get_json(silent=True) or {}


def _require_auth_fields(data):
    org = (data.get("org") or "").strip().upper()
    token = (data.get("token") or "").strip()
    if not org or not token:
        return None, None, jsonify({"success": False, "error": "ORG and token required"})
    return org, token, None


@app.route("/api/app_opened", methods=["POST"])
def app_opened():
    forward_usage_event(
        {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "event_name": "app_opened",
            **(_json() or {}),
        }
    )
    return jsonify({"success": True})


@app.route("/api/auth", methods=["POST"])
def auth():
    data = _json()
    org = (data.get("org") or DEFAULT_ORG).strip().upper()
    if not org:
        return jsonify({"success": False, "error": "ORG required"})
    if not validate_org(org):
        return jsonify(
            {"success": False, "error": "Invalid ORG. Must end with -DEMO (e.g. SS-DEMO)."}
        )
    token, source = resolve_bearer_token(org)
    if token:
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "auth_success",
                "org": org,
                "source": source,
            }
        )
        return jsonify(
            {
                "success": True,
                "token": token,
                "org": org,
                "source": source,
                "fromTokenFile": source == "token-file",
            }
        )
    forward_usage_event(
        {"app_name": APP_NAME, "app_version": APP_VERSION, "event_name": "auth_failed", "org": org}
    )
    has_oauth = bool(PASSWORD and CLIENT_SECRET)
    has_file = TOKEN_FILE.is_file()
    hint = (
        "Auth failed. Place a Bearer token in .token (local), "
        "or set MANHATTAN_PASSWORD / MANHATTAN_SECRET."
    )
    if has_file and not has_oauth:
        hint = "Auth failed reading .token (empty or invalid)."
    elif not has_file and not has_oauth:
        hint = "No .token file and MANHATTAN_PASSWORD / MANHATTAN_SECRET are not set."
    return jsonify({"success": False, "error": hint})


@app.route("/api/preload_asns", methods=["POST"])
def preload_asns():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    try:
        result = preload_asn_index(token, org, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_ASNS] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_staging_locations", methods=["POST"])
def preload_staging_locations_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    try:
        result = preload_staging_locations(token, org, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_STAGING_LOCATIONS] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/preload_transactions", methods=["POST"])
def preload_transactions_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    try:
        result = preload_transactions(token, org, location=location)
        return jsonify(result)
    except Exception as e:
        print(f"[PRELOAD_TRANSACTIONS] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/load_asn", methods=["POST"])
def load_asn():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    asn_id = (data.get("asnId") or data.get("asn_id") or "").strip()
    try:
        result = load_asn_for_receiving(token, org, asn_id, location=location)
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "load_asn_completed" if result.get("success") else "load_asn_failed",
                "org": org,
                "asnId": asn_id,
            }
        )
        return jsonify(result)
    except Exception as e:
        print(f"[LOAD_ASN] {e}")
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "load_asn_failed",
                "org": org,
                "asnId": asn_id,
                "error": str(e),
            }
        )
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/receive_line", methods=["POST"])
def receive_line_route():
    data = _json()
    org, token, err = _require_auth_fields(data)
    if err:
        return err
    location = (data.get("location") or data.get("facility") or "").strip() or None
    asn_id = (data.get("asnId") or data.get("asn_id") or "").strip()
    asn_line_id = (data.get("asnLineId") or data.get("asn_line_id") or "").strip()
    mode = (data.get("mode") or "").strip().lower()
    quantity = data.get("quantity")
    staging_location_id = (data.get("stagingLocationId") or data.get("staging_location_id") or "").strip()
    transaction_id = (data.get("transactionId") or data.get("transaction_id") or "").strip()
    receiving_strategy = (data.get("receivingStrategy") or data.get("receiving_strategy") or "").strip()
    if not asn_id or not asn_line_id:
        return jsonify({"success": False, "error": "asnId and asnLineId required"})
    if not transaction_id or not receiving_strategy:
        return jsonify({"success": False, "error": "Transaction ID is required"})
    try:
        result = receive_line(
            token,
            org,
            asn_id,
            asn_line_id,
            mode,
            transaction_id,
            receiving_strategy,
            quantity_display=quantity,
            location=location,
            staging_location_id=staging_location_id or None,
        )
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "receive_line_completed" if result.get("success") else "receive_line_failed",
                "org": org,
                "asnId": asn_id,
                "asnLineId": asn_line_id,
                "mode": mode,
                "receivingStrategy": receiving_strategy,
            }
        )
        return jsonify(result)
    except Exception as e:
        print(f"[RECEIVE_LINE] {e}")
        forward_usage_event(
            {
                "app_name": APP_NAME,
                "app_version": APP_VERSION,
                "event_name": "receive_line_failed",
                "org": org,
                "asnId": asn_id,
                "asnLineId": asn_line_id,
                "error": str(e),
            }
        )
        return jsonify({"success": False, "error": str(e)}), 500


# Local Flask entry (vercel wraps the module)
if __name__ == "__main__":
    app.run(port=5000, debug=True)
