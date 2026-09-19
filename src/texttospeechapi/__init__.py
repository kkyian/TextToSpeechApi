"""Authenticated text-to-speech API for the Mac running this server."""

from collections import deque
import hmac
import math
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
import time

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

MAX_MESSAGE_LENGTH = 500
RATE_LIMIT = 5
RATE_WINDOW = 60
SPEECH_TIMEOUT = 90


def speak_on_mac(message):
    """Pass text over stdin, never through a shell or command arguments."""
    say = shutil.which("say")
    if say is None:
        raise OSError("The macOS say command is not available")
    subprocess.run(
        [say], input=message, text=True, encoding="utf-8", check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=SPEECH_TIMEOUT,
    )


def create_app(*, speaker=None, clock=None):
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 8192
    api_key = os.environ.get("TTS_API_KEY", "")
    if len(api_key.strip()) < 32:
        raise ValueError("Set TTS_API_KEY to a random secret of at least 32 characters. Run uv run tts-create-key first.")
    speaker = speaker or speak_on_mac
    clock = clock or time.monotonic
    speech_lock = threading.Lock()
    rate_lock = threading.Lock()
    recent_requests = deque()

    def error(message, status, *, received=False, retry_after=None):
        response = jsonify(ok=False, received=received, spoken=False, error=message)
        response.status_code = status
        if retry_after is not None:
            response.headers["Retry-After"] = str(retry_after)
        return response

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return error(exc.description, exc.code)

    @app.after_request
    def prevent_caching(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/speak")
    def speak():
        # Parsing and body-size checks are necessary before reading the key.
        if not request.is_json:
            return error("Use Content-Type: application/json", 415)
        data = request.get_json()
        if not isinstance(data, dict):
            return error("Request body must be a JSON object", 400)

        supplied_key = data.get("api_key")
        if not isinstance(supplied_key, str) or not hmac.compare_digest(
            supplied_key.encode("utf-8"), api_key.encode("utf-8")
        ):
            return error("Invalid or missing api_key", 401)

        # Authentication always precedes message validation and speech.
        message = data.get("message")
        if not isinstance(message, str) or not message.strip():
            return error("message must be a non-empty string", 400)
        if len(message) > MAX_MESSAGE_LENGTH:
            return error(f"message must be at most {MAX_MESSAGE_LENGTH} characters", 400)

        # One shared limit protects the speaker even behind a reverse proxy.
        with rate_lock:
            now = clock()
            while recent_requests and now - recent_requests[0] >= RATE_WINDOW:
                recent_requests.popleft()
            if len(recent_requests) >= RATE_LIMIT:
                retry = max(1, math.ceil(RATE_WINDOW - (now - recent_requests[0])))
                return error("Rate limit exceeded: 5 messages per minute", 429, retry_after=retry)
            if not speech_lock.acquire(blocking=False):
                return error("Speaker is busy; try again shortly", 429, retry_after=1)
            recent_requests.append(now)

        try:
            speaker(message.strip())
        except subprocess.TimeoutExpired:
            return error("Speech timed out; playback may have been partial", 504, received=True)
        except (OSError, subprocess.SubprocessError):
            app.logger.warning("Text-to-speech command failed")
            return error("Text-to-speech failed on the server", 503, received=True)
        finally:
            speech_lock.release()
        return jsonify(ok=True, received=True, spoken=True)

    return app


def create_key():
    """Create an owner-only secret file without overwriting an existing one."""
    try:
        fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(".env already exists; keeping your existing key.") from None
    with os.fdopen(fd, "w") as secret_file:
        secret_file.write(f"TTS_API_KEY={secrets.token_urlsafe(32)}\n")
    print("Created a random API key in .env (readable only by your user).")


def main():
    from dotenv import load_dotenv
    from waitress import serve

    load_dotenv(Path.cwd() / ".env", override=False)
    app = create_app()
    print("Text-to-speech API: http://127.0.0.1:8000/speak", flush=True)
    serve(app, host="127.0.0.1", port=8000, threads=4)
