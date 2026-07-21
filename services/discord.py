"""
Discord helpers shared across service modules: response-type constants,
an option extractor, and the followup-webhook POST helper.

Kept separate from `app.py` so service modules can call these without
importing the Flask app (which would create a circular import).
"""
import requests
from flask import jsonify

# Discord interaction types.
INTERACTION_PING = 1
INTERACTION_APPLICATION_COMMAND = 2
INTERACTION_MESSAGE_COMPONENT = 3

# Discord interaction response types.
RESPONSE_PONG = 1
RESPONSE_CHANNEL_MESSAGE = 4  # reply with a message
RESPONSE_DEFERRED = 5  # ack; send the real reply later via the webhook


def option(data, name, default=None):
    """Extract a slash-command option value from the interaction `data`."""
    for opt in (data.get("options") or []):
        if opt.get("name") == name:
            return opt.get("value")
    return default


def message_response(content, ephemeral=False):
    """Build a synchronous CHANNEL_MESSAGE response with the given content."""
    payload = {"content": content}
    if ephemeral:
        payload["flags"] = 64  # Ephemeral flag
    return jsonify({"type": RESPONSE_CHANNEL_MESSAGE, "data": payload})


def deferred_response():
    """Build a DEFERRED response (ack; send the real reply via the webhook)."""
    return jsonify({"type": RESPONSE_DEFERRED})


def post_followup(webhook_url, content, logger=None):
    """POST a followup message to a Discord interaction webhook."""
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=10)
        if not r.ok and logger:
            logger.warning("Discord followup failed: %s %s",
                           r.status_code, r.text[:300])
    except requests.exceptions.RequestException as e:
        if logger:
            logger.warning("Discord followup request error: %s", e)


def webhook_url_for(app_id, token):
    """Build the followup webhook URL for an interaction."""
    return "https://discord.com/api/webhooks/{}/{}".format(app_id, token)