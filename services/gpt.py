"""
GPT service: owns all interaction with the ml-runner backing service and
implements the `/gpt` (generate) and `/gpt-sets` (list sets) Discord commands
plus the direct-HTTP `/gpt` proxy route.

The ml-runner client is a thin wrapper over requests; the generation queue
serializes Discord /gpt batches so two users hitting /gpt count:10 at once
produce A1..A10 then B1..B10 (not interleaved). See super_bot/README.md for
the per-pod-queue caveat when replicas > 1.
"""
import os
import queue
import threading

import requests
from flask import Response, jsonify, request

from services import discord as d

ML_RUNNER_URL = os.environ.get("ML_RUNNER_URL", "http://localhost:7070")
REQUEST_TIMEOUT = 600  # ml-runner's gunicorn timeout is 600s; match it.

# Max generations a single /gpt invocation can request. Discord enforces the
# min/max at the command-definition level (see register_commands.py), so we
# also clamp here defensively.
MAX_GENERATIONS = 10

# A single-worker queue that serializes /gpt batches across all users. One
# worker pulls jobs FIFO and runs each batch to completion before the next.
_gen_queue = queue.Queue()


# -------------------------------------------------------------------
# ml-runner client
# -------------------------------------------------------------------

def generate(set_name, prefix=""):
    """Call ml-runner /generate synchronously. Returns (ok, text_or_error)."""
    try:
        r = requests.post(
            f"{ML_RUNNER_URL}/generate",
            data={"set": set_name, "prefix": prefix},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        return False, "ml-runner request failed: {}".format(e)
    if not r.ok:
        return False, "ml-runner /generate returned {}: {}".format(
            r.status_code, r.text[:500])
    return True, r.text


def list_sets():
    """Call ml-runner /sets. Returns (ok, json_or_error)."""
    try:
        r = requests.get(f"{ML_RUNNER_URL}/sets", timeout=5)
    except requests.exceptions.RequestException as e:
        return False, "Could not reach ml-runner: {}".format(e)
    if not r.ok:
        return False, "ml-runner /sets returned {}".format(r.status_code)
    try:
        return True, r.json()
    except ValueError:
        return False, "ml-runner /sets returned non-JSON"


def health():
    """Call ml-runner /health. Returns (ok, json_or_error)."""
    try:
        r = requests.get(f"{ML_RUNNER_URL}/health", timeout=5)
        return r.ok, r.json() if r.ok else r.text
    except Exception as e:
        return False, str(e)


# -------------------------------------------------------------------
# Direct-HTTP /gpt proxy route
# -------------------------------------------------------------------

def http_gpt_proxy():
    """
    Proxy to ml-runner's /generate. Accepts the same parameters in the same
    forms as ml-runner: GET query string, POST JSON, or POST form-encoded.
    Returns the ml-runner response verbatim.
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

    content_type = upstream.headers.get("Content-Type", "text/plain")
    return Response(upstream.content, status=upstream.status_code,
                    mimetype=content_type.split(";")[0].strip())


# -------------------------------------------------------------------
# Discord /gpt command (deferred + serialized batch generation)
# -------------------------------------------------------------------

def handle_gpt_command(payload, data, logger):
    """
    /gpt command: `set` (required) + `prefix` (optional) + `count`
    (integer 1..10, optional, default 1).

    Enqueues a batch onto the single-worker _gen_queue and responds DEFERRED
    within Discord's 3s window. The worker runs each batch serially and posts
    each result as a followup as soon as it returns.
    """
    set_name = d.option(data, "set")
    prefix = d.option(data, "prefix", "") or ""
    count = d.option(data, "count", 1)
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(MAX_GENERATIONS, count))

    if not set_name:
        return d.message_response(
            "You must provide a `set` (e.g. `/gpt set:trump-tweet`).")

    token = payload.get("token")
    app_id = payload.get("application_id") or os.environ.get(
        "DISCORD_APPLICATION_ID", "")

    if not token or not app_id:
        return d.message_response("Internal error: missing interaction token.")

    _gen_queue.put((app_id, token, set_name, prefix, count))
    logger.info("Enqueued /gpt batch: set=%s prefix=%r count=%d",
                set_name, prefix[:40], count)
    return d.deferred_response()


def _gpt_worker(logger):
    """Background worker that drains _gen_queue serially. Runs forever."""
    while True:
        app_id, token, set_name, prefix, count = _gen_queue.get()
        try:
            _run_batch(app_id, token, set_name, prefix, count, logger)
        except Exception as e:
            logger.exception("/gpt worker job crashed: %s", e)
        finally:
            _gen_queue.task_done()


def _run_batch(app_id, token, set_name, prefix, count, logger):
    """Run `count` generations serially, posting each result as a followup."""
    webhook_url = d.webhook_url_for(app_id, token)
    for i in range(count):
        ok, result = generate(set_name, prefix)
        if not ok:
            d.post_followup(webhook_url,
                            "GPT generation {} failed: {}".format(i + 1, result),
                            logger=logger)
            return

        text = result.strip()
        if not text:
            text = "_(generated text was empty)_"
        if len(text) > 1900:  # Discord's 2000-char message limit.
            text = text[:1900] + "…"
        # Prefix multi-generation results with an index so the user can tell
        # them apart in the channel.
        label = "" if count == 1 else "**[{}]**\n".format(i + 1)
        d.post_followup(webhook_url, "{}```\n{}\n```".format(label, text),
                        logger=logger)


def start_worker(logger):
    """Start the single generation worker (call once at app startup)."""
    threading.Thread(target=_gpt_worker, args=(logger,), daemon=True).start()


# -------------------------------------------------------------------
# Discord /gpt-sets command (synchronous)
# -------------------------------------------------------------------

def handle_gpt_sets_command(payload, data, logger):
    """
    /gpt-sets command: no options. Synchronously fetches ml-runner /sets and
    returns the set list as a Discord message. Fast enough (one quick HTTP
    GET) to respond within Discord's 3s window.
    """
    ok, result = list_sets()
    if not ok:
        logger.warning("/gpt-sets upstream fetch failed: %s", result)
        return d.message_response(result)

    sets = result.get("sets") or []
    if not sets:
        return d.message_response("No trained sets found.")

    lines = ["**Trained sets** (`/gpt set:<name> ...`):", ""]
    for s in sets:
        nm = s.get("name", "?")
        trained = "\u2705" if s.get("trained") else "\u274c"
        desc = s.get("description") or ""
        lines.append("{} `{}`{}".format(
            trained, nm,
            " \u2014 {}".format(desc) if desc else ""))
    content = "\n".join(lines)
    if len(content) > 1990:  # Discord's 2000-char message limit.
        content = content[:1990] + "…"
    return d.message_response(content)