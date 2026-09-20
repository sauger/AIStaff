# Fish Audio free TTS tryout (`s2.1-pro-free`)

Standalone local demo for evaluating Fish Audio text-to-speech. **Not** wired into RapidStaff / AIStaff production, packaging, or Harness.

## Quick start

```bash
cd demos/fishaudio
python3 server.py
```

Open **http://127.0.0.1:8765** in your browser.

1. Get a free API key at [fish.audio/app/api-keys](https://fish.audio/app/api-keys).
2. Paste the key into the page (or set `FISH_AUDIO_API_KEY` before starting the server).
3. Enter Chinese text and click **生成语音** to synthesize and play MP3 audio.

### Voice clone (instant)

1. Check **用我自己的声音**.
2. Click **填入朗读提示稿**, then **开始录音** for 10–30 seconds (quiet, solo speech), or upload a reference file.
3. Fill in the transcript that matches the recording, then **生成语音**.

Only clone voices you have the right to use. Reference audio is sent inline per request (not saved to disk).

Optional: `export FISH_AUDIO_API_KEY='your-key'` so the page key field can stay empty.

Health check: `curl http://127.0.0.1:8765/health`

## Model and usage

- Model: **`s2.1-pro-free` only** ($0 fair-use tier).
- A local Python proxy avoids browser CORS when calling `https://api.fish.audio/v1/tts`.
- API keys are **not** written to disk or committed to git.

## Non-commercial / safety

- Web Free and this demo are for **personal, non-commercial** evaluation.
- Do not paste customer email, credentials, or secrets into the synthesis text.
- Do not commit API keys or `.env` files.

Without a key, use the official web tryout at [fish.audio/app](https://fish.audio/app).
