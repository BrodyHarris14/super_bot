"""
super_bot: a thin Flask router that proxies external requests to backing
microservices on the home server.

Right now the only backing service is the GPT-2 generation endpoint on
ml-runner (http://<host>:7070/generate). super_bot forwards the exact same
parameters in the exact same forms (query string, JSON body, or form body)
and returns the ml-runner response verbatim.

Env vars:
    PORT            port to listen on (default 8080)
    ML_RUNNER_URL    base URL of ml-runner (default http://localhost:7070)
"""
import os

import requests
from flask import Flask, Response, jsonify, request

app = Flask(__name__)

ML_RUNNER_URL = os.environ.get("ML_RUNNER_URL", "http://localhost:7070")
PORT = int(os.environ.get("PORT", "8080"))
REQUEST_TIMEOUT = 600  # ml-runner's gunicorn timeout is 600s; match it.

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
# Local weather (carried over from the Java skeleton)
# -------------------------------------------------------------------

@app.route("/localWeather", methods=["GET"])
def local_weather():
    """Proxy the Open-Meteo current-temperature endpoint (env-configurable)."""
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)