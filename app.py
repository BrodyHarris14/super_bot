"""
super_bot: a thin Flask router that proxies external requests to backing
microservices on the home server and dispatches Discord slash commands.

Each backing service lives in services/<name>.py and owns its routes / Discord
command handlers + the client logic for that service. This file is just the
router: the Flask app, request logging, the Discord signature verification,
and the command dispatch table.

Env vars:
    PORT                    port to listen on (default 8080)
    ML_RUNNER_URL           base URL of ml-runner (default http://localhost:7070)
    DISCORD_PUBLIC_KEY       Ed25519 public key from the Discord app (defaults to the
                             OUT OF OFFICE app key; override only for testing)
    DISCORD_APPLICATION_ID   Discord application id (defaults to the OUT OF OFFICE
                             app id; override only for testing)
"""
import os

import requests
from flask import Flask, Response, jsonify, request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from services import discord as d
from services import gpt as gpt_service

app = Flask(__name__)

PORT = int(os.environ.get("PORT", "8080"))

# Discord app credentials for the OUT OF OFFICE server. Both are non-secret
# (the public key verifies signatures, the application id is public), so they
# live here as defaults. Override via env only for local testing with a
# different app.
DISCORD_PUBLIC_KEY = os.environ.get(
    "DISCORD_PUBLIC_KEY",
    "39df18124bc34d84d1ba2f3c7843fa0bcfce575b8ed685ed26c00e13428aa04f",
)
DISCORD_APPLICATION_ID = os.environ.get("DISCORD_APPLICATION_ID", "1434750818245939281")

# Start the GPT generation worker once at import time.
gpt_service.start_worker(app.logger)


# -------------------------------------------------------------------
# Request logging
# -------------------------------------------------------------------

@app.before_request
def _log_request():
    """Log every incoming request: method, path, and origin IP."""
    origin = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    app.logger.info("%s %s from %s", request.method, request.path, origin)


# -------------------------------------------------------------------
# Health / meta
# -------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Liveness check + a quick ping to the backing ml-runner."""
    ok, detail = gpt_service.health()
    return jsonify({
        "status": "ok",
        "upstream": gpt_service.ML_RUNNER_URL,
        "upstream_ok": ok,
        "upstream_detail": detail,
    })


# -------------------------------------------------------------------
# GPT proxy: forwards /gpt -> ml-runner /generate (direct HTTP)
# -------------------------------------------------------------------

@app.route("/gpt", methods=["GET", "POST"])
def gpt_proxy():
    """Direct HTTP proxy to ml-runner's /generate (same params/forms)."""
    return gpt_service.http_gpt_proxy()


# -------------------------------------------------------------------
# Local weather (carried over from the Java skeleton)
# -------------------------------------------------------------------

@app.route("/localWeather", methods=["GET"])
def local_weather():
    """Proxy the Open-Meteo current-temperature endpoint."""
    import urllib.request
    lat = os.environ.get("WEATHER_LAT", "39.7392")
    lon = os.environ.get("WEATHER_LON", "-104.9903")
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={lat}&longitude={lon}&current=temperature_2m")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return Response(resp.read(), mimetype="application/json")
    except Exception as e:
        return jsonify({"error": "weather lookup failed", "detail": str(e)}), 500


# -------------------------------------------------------------------
# Discord interactions endpoint (/discord)
# -------------------------------------------------------------------

def _verify_discord_signature():
    """Verify the Discord Ed25519 signature on the incoming request."""
    sig = request.headers.get("X-Signature-Ed25519", "")
    ts = request.headers.get("X-Signature-Timestamp", "")
    body = request.get_data()

    if not DISCORD_PUBLIC_KEY:
        return False, body, "DISCORD_PUBLIC_KEY env var is not set"
    if not sig or not ts:
        return False, body, "missing signature headers"

    try:
        verify_key = VerifyKey(bytes.fromhex(DISCORD_PUBLIC_KEY))
        verify_key.verify(ts.encode() + body, bytes.fromhex(sig))
    except (BadSignatureError, ValueError, TypeError) as e:
        return False, body, "invalid signature: {}".format(e)
    return True, body, None


@app.route("/discord", methods=["POST"])
def discord_interactions():
    """Discord Interactions Endpoint: PING + slash command dispatch."""
    ok, body, reason = _verify_discord_signature()
    if not ok:
        app.logger.warning("Discord signature verification failed: %s", reason)
        return jsonify({"error": "invalid request signature"}), 401

    try:
        payload = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "invalid JSON body"}), 400
    if not isinstance(payload, dict):
        return jsonify({"error": "body must be a JSON object"}), 400

    itype = payload.get("type")

    if itype == d.INTERACTION_PING:
        app.logger.info("Discord PING (handshake)")
        return jsonify({"type": d.RESPONSE_PONG})

    if itype == d.INTERACTION_APPLICATION_COMMAND:
        cmd_name = ((payload.get("data") or {}).get("name") or "?")
        app.logger.info("Discord command: /%s", cmd_name)
        return _dispatch_command(payload)

    app.logger.info("Unhandled interaction type %s", itype)
    return jsonify({"type": d.RESPONSE_DEFERRED})


# Command dispatch table. Each entry maps a slash-command name to a handler
# in a services/<name>.py module. Add new commands here as services grow.
_COMMANDS = {
    "gpt": gpt_service.handle_gpt_command,
    "gpt-sets": gpt_service.handle_gpt_sets_command,
}


def _dispatch_command(payload):
    """Route an APPLICATION_COMMAND interaction to the right service handler."""
    data = payload.get("data") or {}
    name = data.get("name", "")
    handler = _COMMANDS.get(name)
    if handler:
        return handler(payload, data, app.logger)
    app.logger.info("Unknown command: %s", name)
    return d.message_response("Command `{}` is not implemented yet.".format(name))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
