#!/usr/bin/env python3
"""Fish Audio s2.1-pro-free TTS demo — local proxy (no keys written to disk)."""

from __future__ import annotations

import base64
import json
import os
import struct
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
FISH_TTS_URL = "https://api.fish.audio/v1/tts"
MODEL = "s2.1-pro-free"
MIN_REFERENCE_SECONDS = 10.0
MAX_REFERENCE_SECONDS = 30.0
STATIC_DIR = Path(__file__).resolve().parent


def _pack_msgpack(obj) -> bytes:
    """Minimal MessagePack encoder for Fish TTS requests with binary references."""

    def pack_str(s: str) -> bytes:
        data = s.encode("utf-8")
        n = len(data)
        if n <= 31:
            return bytes([0xa0 + n]) + data
        if n <= 0xff:
            return b"\xd9" + bytes([n]) + data
        if n <= 0xffff:
            return b"\xda" + struct.pack(">H", n) + data
        return b"\xdb" + struct.pack(">I", n) + data

    def pack_bin(data: bytes) -> bytes:
        n = len(data)
        if n <= 0xff:
            return b"\xc4" + bytes([n]) + data
        if n <= 0xffff:
            return b"\xc5" + struct.pack(">H", n) + data
        return b"\xc6" + struct.pack(">I", n) + data

    def pack_int(n: int) -> bytes:
        if 0 <= n <= 127:
            return bytes([n])
        if -32 <= n <= -1:
            return bytes([0xe0 + (n + 32)])
        if 0 <= n <= 0xff:
            return b"\xcc" + bytes([n])
        if 0 <= n <= 0xffff:
            return b"\xcd" + struct.pack(">H", n)
        return b"\xce" + struct.pack(">I", n)

    def pack_float(f: float) -> bytes:
        return b"\xcb" + struct.pack(">d", f)

    def pack_bool(b: bool) -> bytes:
        return b"\xc3" if b else b"\xc2"

    def pack_list(items: list) -> bytes:
        n = len(items)
        if n <= 15:
            out = bytes([0x90 + n])
        elif n <= 0xffff:
            out = b"\xdc" + struct.pack(">H", n)
        else:
            out = b"\xdd" + struct.pack(">I", n)
        for item in items:
            out += pack(item)
        return out

    def pack_dict(d: dict) -> bytes:
        n = len(d)
        if n <= 15:
            out = bytes([0x80 + n])
        elif n <= 0xffff:
            out = b"\xde" + struct.pack(">H", n)
        else:
            out = b"\xdf" + struct.pack(">I", n)
        for key, value in d.items():
            out += pack_str(key) + pack(value)
        return out

    def pack(obj) -> bytes:
        if obj is None:
            return b"\xc0"
        if obj is True:
            return pack_bool(True)
        if obj is False:
            return pack_bool(False)
        if isinstance(obj, int):
            return pack_int(obj)
        if isinstance(obj, float):
            return pack_float(obj)
        if isinstance(obj, str):
            return pack_str(obj)
        if isinstance(obj, (bytes, bytearray)):
            return pack_bin(bytes(obj))
        if isinstance(obj, list):
            return pack_list(obj)
        if isinstance(obj, dict):
            return pack_dict(obj)
        raise TypeError(f"unsupported msgpack type: {type(obj)!r}")

    return pack(obj)


class DemoHandler(BaseHTTPRequestHandler):
    server_version = "FishAudioDemo/1.1"

    def log_message(self, fmt: str, *args) -> None:
        if args and isinstance(args[0], str) and args[0].startswith("POST /api/tts"):
            print("[demo] POST /api/tts")
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
            self._send_json(
                200,
                {
                    "ok": True,
                    "model": MODEL,
                    "voice_clone": True,
                    "min_reference_seconds": MIN_REFERENCE_SECONDS,
                },
            )
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

        use_clone = bool(payload.get("use_clone"))
        references = None
        if use_clone:
            ref_b64 = (payload.get("reference_audio_b64") or "").strip()
            ref_text = (payload.get("reference_text") or "").strip()
            ref_seconds = payload.get("reference_duration")

            if not ref_b64:
                self._send_json(400, {"error": "请先录音或上传参考音频"})
                return
            if not ref_text:
                self._send_json(
                    400,
                    {
                        "error": "请填写参考音频的朗读文字",
                        "hint": "需与录音内容尽量一致，Fish Audio 才能克隆音色",
                    },
                )
                return

            try:
                ref_audio = base64.b64decode(ref_b64, validate=True)
            except (ValueError, base64.binascii.Error):
                self._send_json(400, {"error": "参考音频数据无效，请重新录音或上传"})
                return

            if isinstance(ref_seconds, (int, float)):
                if ref_seconds < MIN_REFERENCE_SECONDS:
                    self._send_json(
                        400,
                        {
                            "error": f"参考音频太短，至少需要 {int(MIN_REFERENCE_SECONDS)} 秒",
                            "hint": "安静环境、单人说话，建议录 10–30 秒",
                        },
                    )
                    return
                if ref_seconds > MAX_REFERENCE_SECONDS + 5:
                    self._send_json(
                        400,
                        {
                            "error": f"参考音频建议不超过 {int(MAX_REFERENCE_SECONDS)} 秒",
                            "hint": "演示只需一段清晰样本即可",
                        },
                    )
                    return

            references = [{"audio": ref_audio, "text": ref_text}]

        fish_payload: dict = {"text": text, "format": "mp3"}
        content_type = "application/json"
        if references:
            fish_payload["references"] = references
            fish_body = _pack_msgpack(fish_payload)
            content_type = "application/msgpack"
        else:
            fish_body = json.dumps(fish_payload, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            FISH_TTS_URL,
            data=fish_body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
                "model": MODEL,
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                audio = resp.read()
                out_type = resp.headers.get("Content-Type", "audio/mpeg")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            self._send_json(exc.code, {"error": f"Fish Audio 返回 {exc.code}", "detail": detail})
            return
        except urllib.error.URLError as exc:
            self._send_json(502, {"error": "无法连接 Fish Audio", "detail": str(exc.reason)})
            return

        self.send_response(200)
        self.send_header("Content-Type", out_type)
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-Fish-Model", MODEL)
        self.send_header("X-Voice-Clone", "1" if references else "0")
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
    print("Voice clone: instant (references via MessagePack)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    main()
