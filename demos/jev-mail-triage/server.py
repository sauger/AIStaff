#!/usr/bin/env python3
"""Jev mail triage tryout — local IMAP cache + TypeSafe System One (no OpenClaw)."""

from __future__ import annotations

import email
import email.policy
import html
import imaplib
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from choice_defaults import DEFAULT_CHOICE_CONFIG, choice_config_to_jev_questions

HOST = "127.0.0.1"
PORT = 8766
JEV_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
STATIC_DIR = Path(__file__).resolve().parent
DATA_DIR = STATIC_DIR / "data"
DB_PATH = DATA_DIR / "mail_cache.sqlite"
CHOICE_PATH = DATA_DIR / "choice_config.json"
SNIPPET_MIN = 500
SNIPPET_MAX = 800
SUBJECT_MAX = 200

SECRET_LINE = re.compile(
    r"(?i)(password|passwd|密码|api[_-]?key|secret|验证码|otp|2fa|token\s*[:=])"
)

_imap_session_lock = threading.Lock()
_imap_session: dict[str, Any] = {}


def _load_dotenv() -> None:
    env_path = STATIC_DIR / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = val


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _db_connect() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            uid TEXT NOT NULL,
            from_addr TEXT NOT NULL,
            subject TEXT NOT NULL,
            received_at TEXT NOT NULL,
            snippet TEXT NOT NULL,
            has_attachment INTEGER NOT NULL DEFAULT 0,
            attachment_names TEXT NOT NULL DEFAULT '[]',
            fetched_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def _decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(value):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return " ".join(parts).strip()


def _html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _redact_secrets(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        if SECRET_LINE.search(line):
            lines.append("[已脱敏：可能含敏感信息]")
        else:
            lines.append(line)
    return "\n".join(lines)


def _truncate_snippet(text: str) -> str:
    text = _redact_secrets(text.strip())
    if len(text) <= SNIPPET_MAX:
        return text
    cut = text[:SNIPPET_MAX]
    last_break = max(cut.rfind("\n"), cut.rfind("。"), cut.rfind(". "))
    if last_break >= SNIPPET_MIN:
        return cut[: last_break + 1].strip()
    return cut.strip() + "…"


def _extract_body(msg: email.message.Message) -> tuple[str, bool, list[str]]:
    attachments: list[str] = []
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get_content_disposition() or "")
            filename = part.get_filename()
            if filename:
                attachments.append(_decode_mime_header(filename))
                continue
            if disp == "attachment":
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = part.get_content_charset() or "utf-8"
                payload = payload.decode(charset, errors="replace")
            if not isinstance(payload, str):
                continue
            if ctype == "text/plain":
                plain_parts.append(payload)
            elif ctype == "text/html":
                html_parts.append(payload)
    else:
        try:
            payload = msg.get_content()
        except Exception:
            raw = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            payload = raw.decode(charset, errors="replace") if isinstance(raw, bytes) else str(raw or "")
        if isinstance(payload, str):
            if msg.get_content_type() == "text/html":
                html_parts.append(payload)
            else:
                plain_parts.append(payload)

    body = "\n\n".join(plain_parts).strip()
    if not body and html_parts:
        body = _html_to_text("\n".join(html_parts))
    return body, bool(attachments), attachments


def _message_state(row: sqlite3.Row, openclaw_skills: list[str]) -> dict[str, Any]:
    subject = (row["subject"] or "")[:SUBJECT_MAX]
    snippet = _truncate_snippet(row["snippet"] or "")
    try:
        att_names = json.loads(row["attachment_names"] or "[]")
    except json.JSONDecodeError:
        att_names = []
    return {
        "from": row["from_addr"],
        "subject": subject,
        "snippet": snippet,
        "received_at": row["received_at"],
        "has_attachment": bool(row["has_attachment"]),
        "attachment_names": att_names,
        "openclaw_skills": openclaw_skills,
    }


def _load_choice_config() -> dict:
    if CHOICE_PATH.is_file():
        try:
            data = json.loads(CHOICE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
        except json.JSONDecodeError:
            pass
    return json.loads(json.dumps(DEFAULT_CHOICE_CONFIG, ensure_ascii=False))


def _save_choice_config(config: dict) -> None:
    _ensure_data_dir()
    CHOICE_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_typesafe_key(body: dict) -> str:
    return (body.get("typesafe_api_key") or "").strip() or os.environ.get("TYPESAFE_API_KEY", "").strip()


def _resolve_imap_config(body: dict) -> dict[str, Any]:
    with _imap_session_lock:
        session = dict(_imap_session)
    env = {
        "host": os.environ.get("IMAP_HOST", "").strip(),
        "port": int(os.environ.get("IMAP_PORT", "993") or 993),
        "user": os.environ.get("IMAP_USER", "").strip(),
        "password": os.environ.get("IMAP_PASSWORD", "").strip(),
        "folder": os.environ.get("IMAP_FOLDER", "INBOX").strip() or "INBOX",
        "ssl": os.environ.get("IMAP_SSL", "true").strip().lower() not in ("0", "false", "no"),
    }
    posted = body.get("imap") if isinstance(body.get("imap"), dict) else {}
    for key in ("host", "user", "password", "folder"):
        val = (posted.get(key) or "").strip()
        if val:
            env[key] = val
    if posted.get("port") is not None:
        try:
            env["port"] = int(posted.get("port"))
        except (TypeError, ValueError):
            pass
    if "ssl" in posted:
        env["ssl"] = bool(posted.get("ssl"))
    if (posted.get("password") or "").strip():
        with _imap_session_lock:
            _imap_session["password"] = posted["password"].strip()
    elif session.get("password"):
        env["password"] = session["password"]
    for key in ("host", "user", "folder"):
        if (posted.get(key) or "").strip():
            with _imap_session_lock:
                _imap_session[key] = env[key]
    with _imap_session_lock:
        for key in ("host", "user", "folder", "port", "ssl"):
            if key in _imap_session and key not in posted and key not in ("password",):
                if key == "port":
                    env["port"] = _imap_session[key]
                elif key == "ssl":
                    env["ssl"] = _imap_session[key]
                else:
                    env[key] = _imap_session.get(key, env.get(key))
    return env


def _fetch_imap_messages(cfg: dict[str, Any], count: int) -> list[dict[str, Any]]:
    if not cfg.get("host") or not cfg.get("user") or not cfg.get("password"):
        raise ValueError("IMAP 需要 host、user、password（可在页面填写或写入 .env）")

    if cfg.get("ssl", True):
        client = imaplib.IMAP4_SSL(cfg["host"], cfg.get("port", 993))
    else:
        client = imaplib.IMAP4(cfg["host"], cfg.get("port", 143))

    try:
        client.login(cfg["user"], cfg["password"])
        folder = cfg.get("folder") or "INBOX"
        client.select(f'"{folder}"' if " " in folder else folder, readonly=True)
        typ, data = client.uid("search", None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        pick = uids[-count:] if count < len(uids) else uids
        fetched: list[dict[str, Any]] = []
        now = datetime.now(timezone.utc).isoformat()
        for uid_b in reversed(pick):
            uid = uid_b.decode() if isinstance(uid_b, bytes) else str(uid_b)
            typ, msg_data = client.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            if not isinstance(raw, (bytes, bytearray)):
                continue
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            from_addr = _decode_mime_header(msg.get("From"))
            subject = _decode_mime_header(msg.get("Subject"))[:SUBJECT_MAX]
            date_hdr = msg.get("Date")
            try:
                received = parsedate_to_datetime(date_hdr).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                received = datetime.now(timezone.utc).isoformat()
            body, has_att, att_names = _extract_body(msg)
            snippet = _truncate_snippet(body or subject)
            msg_id = f"{folder}:{uid}"
            fetched.append(
                {
                    "id": msg_id,
                    "uid": uid,
                    "from_addr": from_addr,
                    "subject": subject,
                    "received_at": received,
                    "snippet": snippet,
                    "has_attachment": 1 if has_att else 0,
                    "attachment_names": json.dumps(att_names, ensure_ascii=False),
                    "fetched_at": now,
                }
            )
        return fetched
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _upsert_messages(rows: list[dict[str, Any]]) -> None:
    conn = _db_connect()
    try:
        for row in rows:
            conn.execute(
                """
                INSERT INTO messages (
                    id, uid, from_addr, subject, received_at, snippet,
                    has_attachment, attachment_names, fetched_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    from_addr=excluded.from_addr,
                    subject=excluded.subject,
                    received_at=excluded.received_at,
                    snippet=excluded.snippet,
                    has_attachment=excluded.has_attachment,
                    attachment_names=excluded.attachment_names,
                    fetched_at=excluded.fetched_at
                """,
                (
                    row["id"],
                    row["uid"],
                    row["from_addr"],
                    row["subject"],
                    row["received_at"],
                    row["snippet"],
                    row["has_attachment"],
                    row["attachment_names"],
                    row["fetched_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _list_messages(limit: int = 500) -> list[dict[str, Any]]:
    conn = _db_connect()
    try:
        cur = conn.execute(
            """
            SELECT id, from_addr, subject, received_at, snippet,
                   has_attachment, attachment_names, fetched_at
            FROM messages
            ORDER BY received_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        out: list[dict[str, Any]] = []
        for row in cur:
            out.append(
                {
                    "id": row["id"],
                    "from": row["from_addr"],
                    "subject": row["subject"],
                    "received_at": row["received_at"],
                    "snippet": row["snippet"],
                    "has_attachment": bool(row["has_attachment"]),
                    "attachment_names": json.loads(row["attachment_names"] or "[]"),
                    "fetched_at": row["fetched_at"],
                }
            )
        return out
    finally:
        conn.close()


def _get_messages_by_ids(ids: list[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    conn = _db_connect()
    try:
        placeholders = ",".join("?" for _ in ids)
        cur = conn.execute(
            f"SELECT * FROM messages WHERE id IN ({placeholders})",
            ids,
        )
        return {row["id"]: row for row in cur}
    finally:
        conn.close()


def _call_jev(
    api_key: str,
    state: dict[str, Any],
    questions: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "model": MODEL,
        "state": state,
        "questions": questions,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        JEV_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "JevMailTriageDemo/1.0"

    def log_message(self, fmt: str, *args) -> None:
        msg = fmt % args if args else fmt
        if any(s in msg for s in ("password", "Password", "Bearer")):
            print("[demo] (request omitted — may contain secrets)")
            return
        print(f"[demo] {msg}")

    def _send_json(self, status: int, payload: dict | list) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
            return
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "model": MODEL,
                    "typesafe_key_from_env": bool(os.environ.get("TYPESAFE_API_KEY")),
                },
            )
            return
        if self.path == "/api/messages":
            self._send_json(200, {"messages": _list_messages()})
            return
        if self.path == "/api/choices":
            self._send_json(200, {"choices": _load_choice_config(), "defaults": DEFAULT_CHOICE_CONFIG})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        try:
            if self.path == "/api/fetch":
                self._handle_fetch()
                return
            if self.path == "/api/classify":
                self._handle_classify()
                return
            if self.path == "/api/choices":
                self._handle_save_choices()
                return
            if self.path == "/api/choices/reset":
                _save_choice_config(json.loads(json.dumps(DEFAULT_CHOICE_CONFIG, ensure_ascii=False)))
                self._send_json(200, {"ok": True, "choices": _load_choice_config()})
                return
            self.send_error(404)
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体必须是 JSON"})

    def _handle_fetch(self) -> None:
        body = self._read_json_body()
        try:
            count = int(body.get("count") or 10)
        except (TypeError, ValueError):
            self._send_json(400, {"error": "count 必须是数字"})
            return
        count = max(1, min(count, 200))
        try:
            cfg = _resolve_imap_config(body)
            rows = _fetch_imap_messages(cfg, count)
            _upsert_messages(rows)
            self._send_json(200, {"ok": True, "fetched": len(rows), "messages": _list_messages()})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except imaplib.IMAP4.error as exc:
            self._send_json(502, {"error": "IMAP 错误", "detail": str(exc)[:300]})
        except OSError as exc:
            self._send_json(502, {"error": "无法连接邮箱", "detail": str(exc)[:300]})

    def _handle_save_choices(self) -> None:
        body = self._read_json_body()
        choices = body.get("choices")
        if not isinstance(choices, dict) or not choices:
            self._send_json(400, {"error": "需要 choices 对象"})
            return
        cleaned: dict = {}
        for qid, q in choices.items():
            if not isinstance(q, dict):
                continue
            opts = q.get("options")
            if not isinstance(opts, dict) or not opts:
                continue
            cleaned[str(qid)] = {
                "label": str(q.get("label") or qid),
                "instructions": str(q.get("instructions") or ""),
                "options": {str(k): str(v) for k, v in opts.items()},
            }
        if not cleaned:
            self._send_json(400, {"error": "Choice 配置无效"})
            return
        _save_choice_config(cleaned)
        self._send_json(200, {"ok": True, "choices": cleaned})

    def _handle_classify(self) -> None:
        body = self._read_json_body()
        api_key = _resolve_typesafe_key(body)
        if not api_key:
            self._send_json(
                401,
                {
                    "error": "需要 TypeSafe API Key",
                    "hint": "页面粘贴或设置 TYPESAFE_API_KEY",
                },
            )
            return
        ids = body.get("message_ids")
        if not isinstance(ids, list) or not ids:
            self._send_json(400, {"error": "请选择至少一封邮件"})
            return
        ids = [str(i) for i in ids][:100]
        try:
            concurrency = int(body.get("concurrency") or 2)
        except (TypeError, ValueError):
            concurrency = 2
        concurrency = max(1, min(concurrency, 5))
        skills_raw = body.get("openclaw_skills")
        if isinstance(skills_raw, list):
            skills = [str(s).strip() for s in skills_raw if str(s).strip()]
        elif isinstance(skills_raw, str):
            skills = [s.strip() for s in skills_raw.split(",") if s.strip()]
        else:
            skills = []

        rows = _get_messages_by_ids(ids)
        choice_cfg = _load_choice_config()
        jev_questions = choice_config_to_jev_questions(choice_cfg)

        def classify_one(msg_id: str) -> dict[str, Any]:
            row = rows.get(msg_id)
            if row is None:
                return {"id": msg_id, "ok": False, "error": "本地缓存中找不到该邮件"}
            state = _message_state(row, skills)
            try:
                resp = _call_jev(api_key, state, jev_questions)
                return {"id": msg_id, "ok": True, "answers": resp.get("answers"), "usage": resp.get("usage")}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
                return {"id": msg_id, "ok": False, "error": f"TypeSafe HTTP {exc.code}", "detail": detail}
            except urllib.error.URLError as exc:
                return {"id": msg_id, "ok": False, "error": "无法连接 TypeSafe", "detail": str(exc.reason)}
            except Exception as exc:
                return {"id": msg_id, "ok": False, "error": str(exc)[:200]}

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(classify_one, mid): mid for mid in ids}
            for fut in as_completed(futures):
                results.append(fut.result())
        order = {mid: idx for idx, mid in enumerate(ids)}
        results.sort(key=lambda r: order.get(r["id"], 9999))
        self._send_json(200, {"ok": True, "results": results, "model": MODEL})

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    _load_dotenv()
    _ensure_data_dir()
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Jev mail triage demo → http://{HOST}:{PORT}")
    print(f"Model: {MODEL}")
    print(f"TYPESAFE_API_KEY: {'已设置' if os.environ.get('TYPESAFE_API_KEY') else '未设置（可在页面粘贴）'}")
    print(f"Cache: {DATA_DIR} (gitignored)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
