# api/index.py
import os
import sys
from pathlib import Path

from flask import Flask, jsonify, request
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mawm_client import get_manhattan_token, normalize_token, validate_org  # noqa: E402
from rw_service import load_asn_for_receiving, preload_asn_index, receive_line  # noqa: E402

app = Flask(__name__)

PASSWORD = os.getenv("MANHATTAN_PASSWORD")
CLIENT_SECRET = os.getenv("MANHATTAN_SECRET")
APP_NAME = "receivingworkbench-app"
APP_VERSION = "0.1.3"
DEFAULT_ORG = os.getenv("MANHATTAN_DEFAULT_ORG", "SS-DEMO").strip().upper() or "SS-DEMO"
TOKEN_FILE = ROOT / ".token"


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
        return jsonify(
            {
                "success": True,
                "token": token,
                "org": org,
                "source": source,
                "fromTokenFile": source == "token-file",
            }
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
        return jsonify(result)
    except Exception as e:
        print(f"[LOAD_ASN] {e}")
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
    if not asn_id or not asn_line_id:
        return jsonify({"success": False, "error": "asnId and asnLineId required"})
    try:
        result = receive_line(
            token,
            org,
            asn_id,
            asn_line_id,
            mode,
            quantity_display=quantity,
            location=location,
        )
        return jsonify(result)
    except Exception as e:
        print(f"[RECEIVE_LINE] {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# Local Flask entry (vercel wraps the module)
if __name__ == "__main__":
    app.run(port=5000, debug=True)
