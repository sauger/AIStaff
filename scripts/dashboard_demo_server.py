"""Tiny demo data source for the dashboard capability.

Serves the phase-2 demo contract on localhost so an HTTP Tool can point at it:

    python scripts/dashboard_demo_server.py --port 8765
    # Tool URL: http://127.0.0.1:8765/blocks

Endpoints cover the manual acceptance cases: /blocks (AC-1), /large (AC-2),
/empty (AC-7), /twelve (AC-8), /negative (AC-9), /detail200 (AC-10).
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "dashboard"


def _demo_blocks() -> dict:
    return json.loads((FIXTURES / "demo_blocks.json").read_text(encoding="utf-8"))


def _large() -> dict:
    rows = [[f"2025-{(index % 12) + 1:02d}", 100 + index * 3] for index in range(80)]
    return {
        "as_of": "2026-09-30",
        "blocks": [
            {
                "name": "长序列趋势",
                "columns": [{"name": "month", "type": "string"}, {"name": "sales", "type": "number"}],
                "rows": rows,
            },
            {
                "name": "说明",
                "columns": [{"name": "text", "type": "string"}],
                "rows": [[f"第 {index} 行说明文字，用于把响应撑过 2000 字符。" * 2] for index in range(20)],
            },
        ],
    }


def _twelve() -> dict:
    blocks = [
        {"name": f"指标 {index}", "value": 100 + index, "unit": "万"}
        for index in range(6)
    ]
    blocks += [
        {
            "name": f"分组 {index}",
            "columns": [{"name": "类目", "type": "string"}, {"name": "数值", "type": "number"}],
            "rows": [[f"类目 {row}", (index + 1) * (row + 1)] for row in range(5)],
        }
        for index in range(6)
    ]
    return {"as_of": "2026-09-30", "blocks": blocks}


def _negative() -> dict:
    return {
        "as_of": "2026-09-30",
        "blocks": [
            {
                "name": "利润分布",
                "columns": [{"name": "渠道", "type": "string"}, {"name": "利润", "type": "number"}],
                "rows": [["直营", 120], ["经销", -40], ["线上", 60]],
            }
        ],
    }


def _detail200() -> dict:
    return {
        "as_of": "2026-09-30",
        "blocks": [
            {
                "name": "订单明细",
                "columns": [
                    {"name": "订单号", "type": "string"},
                    {"name": "金额", "type": "number"},
                ],
                "rows": [[f"SO-{index:04d}", index * 10] for index in range(200)],
            }
        ],
    }


ROUTES = {
    "/blocks": _demo_blocks,
    "/large": _large,
    "/empty": lambda: {"as_of": "2026-09-30", "blocks": []},
    "/twelve": _twelve,
    "/negative": _negative,
    "/detail200": _detail200,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        factory = ROUTES.get(self.path.split("?", 1)[0])
        if factory is None:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return
        payload = json.dumps(factory(), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"dashboard demo data on http://127.0.0.1:{args.port}/blocks")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
