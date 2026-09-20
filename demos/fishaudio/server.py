#!/usr/bin/env python3
"""Fish Audio s2.1-pro-free TTS demo — local proxy (no keys written to disk)."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
FISH_TTS_URL = "https://api.fish.audio/v1/tts"
MODEL = "s2.1-pro-free"
STATIC_DIR = Path(__file__).resolve().parent


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "FishAudioDemo/1.0"

    def log_message(self, fmt: str, *args) -> None:
        # Avoid logging request bodies that may contain API keys.
        if args and isinstance(args[0], str) and args[0].startswith("POST /api/tts"):
            print(f"[demo] POST /api/tts")
            return
        print(f"[demo] {fmt % args}")

    def _send_json(self, status: int, payload: dict) -> None:
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
            self._send_json(200, {"ok": True, "model": MODEL})
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if self.path != "/api/tts":
            self.send_error(404)
            return

        try:
            payload = self._read_json_body()
        except json.JSONDecodeError:
            self._send_json(400, {"error": "请求体必须是 JSON"})
            return

        text = (payload.get("text") or "").strip()
        if not text:
            self._send_json(400, {"error": "请输入要合成的文字"})
            return
        if len(text) > 500:
            self._send_json(400, {"error": "演示最多 500 字，请缩短文本"})
            return

        api_key = (payload.get("api_key") or "").strip() or os.environ.get("FISH_AUDIO_API_KEY", "").strip()
        if not api_key:
            self._send_json(
                401,
                {
                    "error": "需要 Fish Audio API Key",
                    "hint": "在页面粘贴 Key，或设置环境变量 FISH_AUDIO_API_KEY 后重启服务",
                },
            )
            return

        fish_body = json.dumps({"text": text, "format": "mp3"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            FISH_TTS_URL,
            data=fish_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "model": MODEL,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                audio = resp.read()
                content_type = resp.headers.get("Content-Type", "audio/mpeg")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            self._send_json(exc.code, {"error": f"Fish Audio 返回 {exc.code}", "detail": detail})
            return
        except urllib.error.URLError as exc:
            self._send_json(502, {"error": "无法连接 Fish Audio", "detail": str(exc.reason)})
            return

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-Fish-Model", MODEL)
        self.end_headers()
        self.wfile.write(audio)

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
    server = ThreadingHTTPServer((HOST, PORT), DemoHandler)
    print(f"Fish Audio demo → http://{HOST}:{PORT}")
    print(f"Model: {MODEL} (free tier)")
    env_key = bool(os.environ.get("FISH_AUDIO_API_KEY"))
    print(f"Env FISH_AUDIO_API_KEY: {'已设置' if env_key else '未设置（可在页面粘贴）'}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
