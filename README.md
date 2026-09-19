# Text-to-speech API

A small Python server that reads messages aloud **on the Mac running the server**.
It uses Flask, Waitress, and macOS's built-in `say` command. No speech service or
paid API is needed. This version requires macOS and Python 3.14 or newer.

## Run locally

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed, open a
terminal in this project folder:

```sh
uv sync
uv run tts-create-key
uv run texttospeechapi
```

Alternatively, with Python 3.14 installed:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tts-create-key
texttospeechapi
```

The server listens at `http://127.0.0.1:8000`. Leave that terminal running; press
Ctrl+C to stop. Make sure the Mac's volume is up and its audio output is correct.

## Send a message

Open the local `.env` file to find your secret and replace `YOUR_SECRET` below.
Do not share this file or put its contents on GitHub. This single-line command
works in Nushell as well as Bash and Zsh:

```sh
curl --max-time 100 -X POST http://127.0.0.1:8000/speak -H 'Content-Type: application/json' -d '{"api_key":"YOUR_SECRET","message":"Hello! This is a test message."}'
```

Speech starts immediately if the speaker is available. The request waits until
the speech command finishes, then returns HTTP 200:

```json
{"ok": true, "received": true, "spoken": true}
```

`spoken: true` means the operating system's speech command completed successfully;
the API cannot detect muted speakers or whether a person heard the audio.

## API behavior

`POST /speak` accepts a JSON object. Authentication is checked before validating
the message or running speech. JSON parsing and the body-size check necessarily
happen before the key can be read.

| Status | Meaning |
| --- | --- |
| 200 | Speech command completed |
| 400 | Malformed JSON, non-object JSON, or missing/invalid message |
| 401 | Missing, incorrect, or non-string `api_key` |
| 413 | Request body exceeds 8 KiB |
| 415 | Request is not sent as JSON |
| 429 | Rate limit reached or speaker already busy; check `Retry-After` |
| 503 | Speech command unavailable or failed |
| 504 | Speech exceeded 90 seconds and was stopped; some audio may have played |

Messages must be nonblank strings of at most 500 characters. Errors return JSON:

```json
{"ok": false, "received": false, "spoken": false, "error": "Invalid or missing api_key"}
```

`received` means an authenticated, valid message was accepted for playback. It is
true for speech failures, but false for validation, authentication, and rate-limit
rejections. Rejected requests never start speech.

The server allows **five accepted messages per rolling 60 seconds**, shared by
all callers. Only one message can play at once; busy requests are rejected rather
than queued. Failed playback attempts count toward the limit. Limits live in
memory and reset on restart. Run one server instance for this prototype. This
protects playback; it is not a network-level denial-of-service defense.

## Keep the API key private

`uv run tts-create-key` generates a cryptographically random 256-bit secret in
`.env`, with owner-only file permissions. It refuses to overwrite an existing
file. Run it once, from this project directory. The server automatically loads
that directory's `.env` on startup. `.env` and `.env.*` secret files are ignored
by Git. Never upload them, paste the secret in a webpage, or commit it.

There is no default key. Missing keys and keys shorter than 32 characters prevent
startup. The old `kkyian1` key no longer works after restarting the server.
An explicitly set `TTS_API_KEY` environment variable takes precedence over `.env`.
If you previously exported an old key, unset it before starting the server
(`hide-env TTS_API_KEY` in Nushell, or `unset TTS_API_KEY` in Bash/Zsh).

To rotate the key, stop the server, delete the local `.env`, run
`uv run tts-create-key`, and restart. Update your clients to use the new secret.
The server does not log keys or message contents. Commands containing a literal
key may be stored in your shell history; keep that history private too.

## Expose a public HTTPS URL with ngrok

Keep this server on your Mac so audio plays there. ngrok forwards a public HTTPS
address to the local server.

1. Generate your key and start the API as above.
2. Install ngrok if needed: `brew install --cask ngrok`.
3. Connect your ngrok account once, using the token from your
   [ngrok dashboard](https://dashboard.ngrok.com/get-started/your-authtoken):

   ```sh
   ngrok config add-authtoken YOUR_NGROK_AUTHTOKEN
   ```

   The ngrok authtoken is separate from your API key. Keep both private.
4. In another terminal, start the tunnel:

   ```sh
   ngrok http http://127.0.0.1:8000 --inspect=false
   ```

5. Use the printed HTTPS URL followed by `/speak` in your request:

   ```sh
   curl --max-time 100 -X POST https://YOUR-DOMAIN.ngrok-free.app/speak -H 'Content-Type: application/json' -d '{"api_key":"YOUR_SECRET","message":"Hello from my API!"}'
   ```

`--inspect=false` disables the local request inspector, so it does not capture
request bodies containing your API key. HTTPS traffic passes through ngrok.
Keep both the API and tunnel running and the Mac awake. Ctrl+C stops each process.
The public URL may change when you restart; use the URL ngrok prints each time.
Anyone with the URL and key can make your Mac speak. A client disconnect or
timeout does not guarantee speech was cancelled; retrying may repeat it.

See the [official ngrok macOS setup instructions](https://ngrok.com/download/mac-os).
The app uses [Waitress](https://flask.palletsprojects.com/en/stable/deploying/waitress/)
and binds only to loopback.

## GitHub Pages limitation

[GitHub Pages only serves static files](https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site).
It cannot run this Python API or access your Mac's speakers. The API must remain
running on your Mac behind an HTTPS tunnel. A separate GitHub Pages frontend
could call that API, but must never contain the secret in its published code.

## Run tests

```sh
uv run python -m unittest discover -s tests -v
```

Tests substitute a fake speaker, so they do not play audio. They check
authentication, validation, playback failures, rate limiting, and overlapping
requests. The curl example above is the real audio smoke test.
