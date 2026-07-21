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
import queue
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

# Discord interaction response types.
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4  # reply with a message
RESPONSE_DEFERRED = 5  # ack; send the real reply later via the webhook

# Max generations a single /gpt invocation can request. Discord enforces the
# min/max at the command-definition level (see register_commands.py), so we
# also clamp here defensively.
MAX_GENERATIONS = 10

# A single-worker queue that serializes /gpt batches across all users. One
# worker pulls jobs FIFO and runs each batch to completion before the next,
# so two users hitting /gpt count:10 at the same time produce A1..A10 then
# B1..B10 (not interleaved). Trivially fair; no locks needed.
_gen_queue = queue.Queue()

# Whitespace-free body types we forward to ml-runner without buffering.
FORWARD_AS_IS = {"application/x-www-form-urlencoded", "multipart/form-data"}


# -------------------------------------------------------------------
# Request logging
# -------------------------------------------------------------------

@app.before_request
def _log_request():
    """Log every incoming request: method, path, and origin IP."""
    # X-Forwarded-For is set by Cloudflare's edge; fall back to remote_addr
    # for direct LAN calls (NodePort) where there's no proxy in front.
    origin = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    app.logger.info("%s %s from %s", request.method, request.path, origin)


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
        app.logger.info("Discord PING (handshake)")
        return jsonify({"type": RESPONSE_PONG})

    if itype == INTERACTION_APPLICATION_COMMAND:
        cmd_name = ((payload.get("data") or {}).get("name") or "?")
        app.logger.info("Discord command: /%s", cmd_name)
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

    if name == "gpt-sets":
        return _handle_gpt_sets_command(payload, data)

    # Unknown command — acknowledge with a deferred so Discord doesn't error,
    # and (best effort) post a "not implemented" followup.
    app.logger.info("Unknown command: %s", name)
    return jsonify({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": "Command `{}` is not implemented yet.".format(name)},
    })


def _handle_gpt_sets_command(payload, data):
    """
    /gpt-sets command: no options. Synchronously fetches ml-runner /sets and
    returns the set list as a Discord message. Fast enough (one quick HTTP
    GET) to respond within Discord's 3s window, so no deferral needed.
    """
    try:
        r = requests.get(f"{ML_RUNNER_URL}/sets", timeout=5)
    except requests.exceptions.RequestException as e:
        app.logger.warning("/gpt-sets upstream fetch failed: %s", e)
        return jsonify({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "Could not reach ml-runner: {}".format(e)},
        })

    if not r.ok:
        return jsonify({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "ml-runner /sets returned {}".format(r.status_code)},
        })

    try:
        body = r.json()
    except ValueError:
        return jsonify({
            "type": RESPONSE_CHANNEL_MESSAGE,
            "data": {"content": "ml-runner /sets returned non-JSON"},
        })

    sets = body.get("sets") or []
    if not sets:
        content = "No trained sets found."
    else:
        lines = ["**Trained sets** (`/gpt set:<name> ...`):", ""]
        for s in sets:
            nm = s.get("name", "?")
            trained = "\u2705" if s.get("trained") else "\u274c"
            desc = s.get("description") or ""
            lines.append("{} `{}`{}".format(
                trained, nm,
                " \u2014 {}".format(desc) if desc else ""))
        content = "\n".join(lines)

    # Discord caps message content at 2000 chars.
    if len(content) > 1990:
        content = content[:1990] + "…"
    return jsonify({
        "type": RESPONSE_CHANNEL_MESSAGE,
        "data": {"content": content},
    })


def _option(data, name, default=None):
    """Extract a slash-command option value from the interaction `data`."""
    for opt in (data.get("options") or []):
        if opt.get("name") == name:
            return opt.get("value")
    return default


def _handle_gpt_command(payload, data):
    """
    /gpt command: `set` (string, required) + `prefix` (string, optional) +
    `count` (integer 1..10, optional, default 1).

    Enqueues a batch onto the single-worker _gen_queue and responds DEFERRED
    within Discord's 3s window. The worker runs each batch serially: it calls
    ml-runner /generate `count` times and posts each result as a separate
    followup message as soon as it returns. Because there's one worker, two
    users hitting /gpt count:10 at once produce A1..A10 then B1..B10.
    """
    set_name = _option(data, "set")
    prefix = _option(data, "prefix", "") or ""
    count = _option(data, "count", 1)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(MAX_GENERATIONS, count))

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

    # Enqueue the batch; the single worker picks it up and streams results.
    _gen_queue.put((app_id, token, set_name, prefix, count))
    app.logger.info("Enqueued /gpt batch: set=%s prefix=%r count=%d",
                    set_name, prefix[:40], count)

    # Acknowledge within the 3s window.
    return jsonify({"type": RESPONSE_DEFERRED})


def _gpt_worker():
    """
    Background worker that drains _gen_queue serially. Each job is one
    /gpt invocation; it runs `count` generations back-to-back, posting each
    result as a followup as soon as it's ready. Runs forever (daemon thread).
    """
    while True:
        app_id, token, set_name, prefix, count = _gen_queue.get()
        try:
            _gpt_followup(app_id, token, set_name, prefix, count)
        except Exception as e:
            app.logger.exception("/gpt worker job crashed: %s", e)
        finally:
            _gen_queue.task_done()


# Start the single worker once at import time.
threading.Thread(target=_gpt_worker, daemon=True).start()


def _gpt_followup(app_id, token, set_name, prefix, count=1):
    """
    Run `count` generations from ml-runner, posting each result as a separate
    followup to the Discord interaction webhook as soon as it returns. The
    webhook is valid for ~15 minutes after the interaction and accepts any
    number of followups. Called by the single _gpt_worker, so batches are
    serialized across users (A1..A10 then B1..B10, not interleaved).
    """
    webhook_url = ("https://discord.com/api/webhooks/{}/{}".format(app_id, token))
    for i in range(count):
        try:
            upstream = requests.post(
                f"{ML_RUNNER_URL}/generate",
                data={"set": set_name, "prefix": prefix},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.exceptions.RequestException as e:
            _post_followup(webhook_url, "GPT generation {} failed: {}".format(i + 1, e))
            return

        if not upstream.ok:
            _post_followup(webhook_url,
                           "GPT generation {} failed (upstream {}): {}".format(
                               i + 1, upstream.status_code, upstream.text[:500]))
            return

        text = upstream.text.strip()
        if not text:
            text = "_(generated text was empty)_"
        # Truncate to Discord's 2000-char message limit.
        if len(text) > 1900:
            text = text[:1900] + "…"
        # Prefix multi-generation results with an index so the user can tell
        # them apart in the channel.
        _post_followup(webhook_url, "{} **{}**/{}:\n> {}".format(set_name, i, count, text))


def _post_followup(webhook_url, content):
    """POST a followup message to a Discord interaction webhook."""
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=10)
        if not r.ok:
            app.logger.warning("Discord followup failed: %s %s",
                               r.status_code, r.text[:300])
    except requests.exceptions.RequestException as e:
        app.logger.warning("Discord followup request error: %s", e)