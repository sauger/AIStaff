# Jev mail triage tryout (`jev-latest`)

Standalone local demo for evaluating TypeSafe Jev on real IMAP mail. **Not** wired into OpenClaw, RapidStaff, or `backend/app`.

## Quick start

```bash
cd demos/jev-mail-triage
cp .env.example .env   # optional — fill IMAP / TypeSafe keys locally, never commit .env
python3 server.py
```

Open **http://127.0.0.1:8766** in your browser.

1. Paste `TYPESAFE_API_KEY` (or set it in `.env` / environment before start).
2. Enter IMAP host, user, password, folder (default `INBOX`).
3. Click **下载到本地缓存** for the last *N* messages (stored under `data/`, gitignored).
4. Select messages → **开始** to run one System One request per mail (three parallel Choice questions).
5. Edit Choice instructions/options in the UI; saves to `data/choice_config.json`. **恢复默认** resets to spec text.

Health check: `curl http://127.0.0.1:8766/health`

## API upstream

- `POST https://api.typesafe.ai/v1/systemone`
- Model: **`jev-latest`**
- Questions: urgency (P0–P3), spam (`spam` / `legit` / `unsure`), handling (`auto` / `notify_only` / `ask_human`)

The local Python server proxies Jev calls (avoids browser CORS) and performs IMAP fetch.

## Safety

- API keys and mailbox passwords are **not** written to git or committed cache fields.
- Cached mail: from / subject / date / redacted snippet only.
- Snippets are truncated (~500–800 chars) and lines matching password-like patterns are redacted.
- Mail content is sent to TypeSafe when you classify — use a test mailbox first.

## Files

| Path | Role |
|------|------|
| `server.py` | HTTP server, IMAP, SQLite cache, Jev proxy |
| `index.html` | Chinese ADHD-friendly UI |
| `choice_defaults.py` | Default Choice text from triage spec |
| `.env.example` | Template for local secrets |
| `data/` | SQLite + choice overrides (gitignored) |

User-facing how-to (Project store): `docs/jev-mail-triage-demo.md` in the AIStaff Project Context.
