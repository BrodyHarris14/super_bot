"""
super_bot: a thin Flask router that proxies external requests to backing
microservices on the home server.

Right now the only backing service is the GPT-2 generation endpoint on
ml-runner (http://<host>:7070/generate). super_bot forwards the exact same
parameters in the exact same forms (query string, JSON body, or form body)
and returns the ml-runner response verbatim.

It also exposes a Discord Interactions Endpoint at /discord that verifies
Discord's Ed25519 signature, handles PING, and dispatches slash commands.
Because GPT generation can exceed Discord's 3s initial-response window, the
/gpt command defers and then POSTs the result to the interaction webhook.

Env vars:
    PORT                    port to listen on (default 8080)
    ML_RUNNER_URL           base URL of ml-runner (default http://localhost:7070)
    DISCORD_PUBLIC_KEY       Ed25519 public key from the Discord app (defaults to the
                             OUT OF OFFICE app key; override only for testing)
    DISCORD_APPLICATION_ID   Discord application id (defaults to the OUT OF OFFICE
                             app id; override only for testing)
"""
import os
import threading

import requests
from flask import Flask, Response, jsonify, request
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

app = Flask(__name__)

ML_RUNNER_URL = os.environ.get("ML_RUNNER_URL", "http://localhost:7070")
PORT = int(os.environ.get("PORT", "8080"))
REQUEST_TIMEOUT = 600  # ml-runner's gunicorn timeout is 600s; match it.

# Discord app credentials for the OUT OF OFFICE server. Both are non-secret
# (the public key verifies signatures, the application id is public), so they
# live here as defaults. Override via env only for local testing with a
# different app.
DISCORD_PUBLIC_KEY = os.environ.get(
    "DISCORD_PUBLIC_KEY",
    "39df18124bc34d84d1ba2f3c7843fa0bcfce575b8ed685ed26c00e13428aa04f",
)
DISCORD_APPLICATION_ID = os.environ.get("DISCORD_APPLICATION_ID", "1434750818245939281")

# Discord interaction types.
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
INTERACTION_MESSAGE_COMPONENT = 3

# Discord interaction response types.end the real reply later via the webhook

# Default GPT set for the /gpt command if the `set` option is omitted.
DEFAULT_GPT_SET = "trump-tweet"

# Whitespace-free body types we forward to ml-runner without buffering.
FORWARD_AS_IS = {"application/x-www-form-urlencoded", "multipart/form-data"}


# -------------------------------------------------------------------
# Health / meta
# -------------------------------------------------------------------

@app.route("/health", methods=["GET"])
def health():
    """Liveness check + a quick ping to the backing ml-runner."""
    upstream_ok = False
    upstream_detail = None
    try:
        r = requests.get(f"{ML_RUNNER_URL}/health", timeout=5)
        upstream_ok = r.ok
        upstream_detail = r.json()
    except Exception as e:
        upstream_detail = str(e)
    return jsonify({
        "status": "ok",
        "upstream": ML_RUNNER_URL,
        "upstream_ok": upstream_ok,
        "upstream_detail": upstream_detail,
    })


# -------------------------------------------------------------------
# GPT proxy: forwards /gpt -> ml-runner /generate (same params, same forms)
# -------------------------------------------------------------------

@app.route("/gpt", methods=["GET", "POST"])
def gpt_proxy():
    """
    Proxy to ml-runner's /generate. Accepts the same parameters in the same
    forms as ml-runner:
      - GET: query string (?set=...&prefix=...&async=true)
      - POST application/json: {"set": ..., "prefix": ..., "async": ...}
      - POST application/x-www-form-urlencoded: form fields

    The response from ml-runner is returned verbatim (plain text for sync
    generates, JSON for async/error).
    """
    if request.method == "GET":
        params = request.args.to_dict()
        return _forward_generate(params=params)

    ctype = request.content_type or ""

    if ctype == "application/json":
        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "JSON body must be an object"}), 400
        return _forward_generate(params=payload)

    if ctype.startswith("application/x-www-form-urlencoded"):
        return _forward_generate(params=request.form.to_dict())

    # Any other content-type (including empty) — try form data, fall back to
    # treating the raw body as the prefix with no set.
    if request.form:
        return _forward_generate(params=request.form.to_dict())

    return jsonify({
        "error": "Unsupported content-type; use query string, JSON, or form data",
    }), 415


def _forward_generate(params):
    """POST the params to ml-runner /generate and return its response."""
    try:
        upstream = requests.post(
            f"{ML_RUNNER_URL}/generate",
            data=params,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        return jsonify({"error": "upstream ml-runner timed out"}), 504
    except requests.exceptions.ConnectionError as e:
        return jsonify({
            "error": "cannot reach upstream ml-runner",
            "detail": str(e),
        }), 502

    # Pass the upstream status code and content-type through.
    content_type = upstream.headers.get("Content-Type", "text/plain")
    return Response(upstream.content, status=upstream.status_code,
                    mimetype=content_type.split(";")[0].strip())


# -------------------------------------------------------------------
# Discord interactions endpoint (/discord)
# -------------------------------------------------------------------

def _verify_discord_signature():
    """
    Verify the Discord Ed25519 signature on the incoming request.

    Discord sends:
        X-Signature-Ed25519: hex ed25519 signature of (timestamp + raw body)
        X-Signature-Timestamp: the timestamp string

    Returns (signature_ok: bool, body_bytes: bytes, reason: str|None).
    """
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
    """
    Discord Interactions Endpoint.

    - PING (type 1): respond with PONG so Discord can validate the URL.
    - APPLICATION_COMMAND (type 2): dispatch on the command name. The /gpt
      command defers (responds within 3s) and then POSTs the generated text
      to the interaction's followup webhook from a background thread.
    """
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

    if itype == INTERACTION_PING:
        return jsonify({"type": RESPONSE_PONG})

    if itype == INTERACTION_APPLICATION_COMMAND:
        return _dispatch_command(payload)

    # Other interaction types (message components, modals, autocomplete) are
    # not handled yet — ack so Discord doesn't flag the endpoint as failing.
    app.logger.info("Unhandled interaction type %s", itype)
    return jsonify({"type": RESPONSE_DEFERRED})


def _dispatch_command(payload):
    """Route an APPLICATION_COMMAND interaction to the right handler."""
    data = payload.get("data") or {}
    name = data.get("name", "")

    if name == "gpt":
        return _handle_gpt_command(payload, data)

    # Unknown command — acknowledge with a deferred so Discord doesn't error,
    # and (best effort) post a "not implemented" followup.
    app.logger.info("Unknown command: %s", name)
    return jsonify({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": "Command `{}` is not implemented yet.".format(name)},
    })


def _option(data, name, default=None):
    """Extract a slash-command option value from the interaction `data`."""
    for opt in (data.get("options") or []):
        if opt.get("name") == name:
            return opt.get("value")
    return default


def _handle_gpt_command(payload, data):
    """
    /gpt command: `set` (string, required) + `prefix` (string, optional).

    GPT generation can take longer than Discord's 3s initial-response window,
    so we immediately respond with DEFERRED and then POST the generated text
    to the interaction's followup webhook (`/webhooks/<app>/<token>`) in a
    background thread once ml-runner returns.
    """
    set_name = _option(data, "set")
    prefix = _option(data, "prefix", "") or ""

    if not set_name:
        return jsonify({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "You must provide a `set` (e.g. `/gpt set:trump-tweet`)."},
        })

    token = payload.get("token")
    app_id = payload.get("application_id") or DISCORD_APPLICATION_ID

    if not token or not app_id:
        return jsonify({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "Internal error: missing interaction token."},
        })

    # Fire the generation + followup in the background.
    t = threading.Thread(
        target=_gpt_followup,
        args=(app_id, token, set_name, prefix),
        daemon=True,
    )
    t.start()

    # Acknowledge within the 3s window.
    return jsonify({"type": RESPONSE_DEFERRED})


def _gpt_followup(app_id, token, set_name, prefix):
    """
    Background thread: call ml-runner /generate, then POST the result to the
    Discord interaction followup webhook. The webhook is valid for ~15 minutes
    after the interaction, and accepts any number of followup messages.
    """
    webhook_url = ("https://discord.com/api/webhooks/{}/{}".format(app_id, token))
    try:
        upstream = requests.post(
            f"{ML_RUNNER_URL}/generate",
            data={"set": set_name, "prefix": prefix},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        _post_followup(webhook_url, "GPT generation failed: {}".format(e))
        return

    if not upstream.ok:
        _post_followup(webhook_url,
                       "GPT generation failed (upstream {}): {}".format(
                           upstream.status_code, upstream.text[:500]))
        return

    text = upstream.text.strip()
    if not text:
        text = "_(generated text was empty)_"
    # Truncate to Discord's 2000-char message limit.
    if len(text) > 1900:
        text = text[:1900] + "…"
    _post_followup(webhook_url, "```\n{}\n```".format(text))


def _post_followup(webhook_url, content):
    """POST a followup message to a Discord interaction webhook."""
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=10)
        if not r.ok:
            app.logger.warning("Discord followup failed: %s %s",
                               r.status_code, r.text[:300])
    except requests.exceptions.RequestException as e:
        app.logger.warning("Discord followup request error: %s", e)