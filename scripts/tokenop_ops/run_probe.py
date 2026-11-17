#!/usr/bin/env python3
"""TokenOp 行为探针：通过本地 StaffDeck 聊天接口发送基准/验收问题并记录遥测。

用法:
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/run_probe.py \
        --probes scripts/tokenop_ops/probes_baseline.json \
        --out /tmp/tokenop_ops/baseline_results.json

凭据来源（二选一，不落盘、不入库）:
    STAFFDECK_TOKEN          已有的 Bearer token
    STAFFDECK_TENANT / STAFFDECK_USERNAME / STAFFDECK_PASSWORD   现场登录换 token

其他环境变量:
    STAFFDECK_URL   本地服务地址，默认 http://127.0.0.1:5173
    TOKENOP_AGENT_ID  默认 agent_aa14eea9e0a143af

探针文件格式（每个探针独立会话，turns 顺序发送）:
    [{"id": "...", "title": "...", "turns": ["...", "..."]}]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


def http_json(url: str, token: str, method: str = "GET", body: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def login(base_url: str, tenant: str, username: str, password: str) -> str:
    payload = json.dumps(
        {"tenant_id": tenant, "username": username, "password": password}
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/api/auth/login", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    token = body.get("token")
    if not token:
        raise RuntimeError(f"login failed: {body}")
    return token


def stream_turn(base_url: str, token: str, session_id: str, agent_id: str, message: str, timeout: float) -> dict:
    """POST /api/chat/stream 并读 SSE 到终止事件，返回 reply/tool 调用/错误/墙钟耗时。"""
    body = json.dumps(
        {
            "tenant_id": os.environ.get("STAFFDECK_TENANT", "tenant_demo"),
            "session_id": session_id,
            "agent_id": agent_id,
            "message": message,
            "channel": "web",
        }
    ).encode()
    req = urllib.request.Request(
        f"{base_url}/api/chat/stream", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
    )
    started = time.monotonic()
    result: dict = {"tool_calls": [], "events": [], "reply": "", "error": None, "terminal": None}
    terminal_events = {"complete", "error_occurred", "stream_cancelled", "stream_interrupted"}
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        event_name = ""
        for raw in resp:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            if line.startswith("event: "):
                event_name = line[len("event: "):].strip()
                result["events"].append(event_name)
                continue
            if not line.startswith("data: "):
                continue
            try:
                payload = json.loads(line[len("data: "):])
            except json.JSONDecodeError:
                continue
            if event_name == "harness_tool_completed":
                name = (
                    payload.get("tool_name")
                    or payload.get("name")
                    or (payload.get("action") or {}).get("name")
                    or ""
                )
                ok = payload.get("success")
                result["tool_calls"].append({"name": name, "success": ok})
            elif event_name == "complete":
                result["reply"] = payload.get("reply") or ""
                result["runtime_error_code"] = payload.get("runtime_error_code")
                result["provider"] = payload.get("provider")
                router = payload.get("router_decision") or {}
                result["router"] = {
                    "decision": router.get("decision"),
                    "target_skill_id": router.get("target_skill_id"),
                    "reason": (router.get("reason") or "")[:200],
                }
            elif event_name == "error_occurred":
                result["error"] = payload
            if event_name in terminal_events:
                result["terminal"] = event_name
                break
    result["wall_ms"] = round((time.monotonic() - started) * 1000)
    return result


def session_telemetry(base_url: str, token: str, session_id: str, tenant: str) -> dict:
    """拉取 events/trace，统计 LLM 调用与服务端耗时。失败不致命。"""
    out: dict = {}
    try:
        events = http_json(
            f"{base_url}/api/chat/sessions/{session_id}/events?tenant_id={tenant}", token
        )
        evs = events if isinstance(events, list) else events.get("events", [])
        llm = [e for e in evs if str(e.get("event_type", "")).startswith("llm_call_finished")]
        out["llm_call_count"] = len(llm)
        out["llm_operations"] = sorted({str(e.get("operation")) for e in llm if e.get("operation")})
        out["llm_models"] = sorted({str(e.get("model_name")) for e in llm if e.get("model_name")})
        out["tool_finished_count"] = sum(
            1 for e in evs if e.get("event_type") == "harness_tool_completed"
        )
    except Exception as exc:  # noqa: BLE001
        out["events_error"] = str(exc)
    try:
        trace = http_json(
            f"{base_url}/api/chat/sessions/{session_id}/trace?tenant_id={tenant}", token
        )
        turns = trace if isinstance(trace, list) else []
        out["server_turn_ms"] = [t.get("duration_ms") for t in turns if t.get("duration_ms")]
    except Exception as exc:  # noqa: BLE001
        out["trace_error"] = str(exc)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probes", required=True, help="探针 JSON 文件路径")
    parser.add_argument("--out", required=True, help="结果 JSON 输出路径")
    parser.add_argument("--turn-timeout", type=float, default=300.0)
    parser.add_argument("--title-prefix", default="")
    args = parser.parse_args()

    base_url = os.environ.get("STAFFDECK_URL", "http://127.0.0.1:5173").rstrip("/")
    tenant = os.environ.get("STAFFDECK_TENANT", "tenant_demo")
    agent_id = os.environ.get("TOKENOP_AGENT_ID", "agent_aa14eea9e0a143af")

    token = os.environ.get("STAFFDECK_TOKEN")
    if not token:
        username = os.environ.get("STAFFDECK_USERNAME")
        password = os.environ.get("STAFFDECK_PASSWORD")
        if not (username and password):
            print("需要 STAFFDECK_TOKEN 或 STAFFDECK_USERNAME/STAFFDECK_PASSWORD", file=sys.stderr)
            return 2
        token = login(base_url, tenant, username, password)

    with open(args.probes, encoding="utf-8") as fh:
        probes = json.load(fh)

    results = []
    for probe in probes:
        probe_id = probe["id"]
        title = probe.get("title", probe_id)
        session = http_json(
            f"{base_url}/api/chat/sessions",
            token,
            method="POST",
            body={
                "tenant_id": tenant,
                "agent_id": agent_id,
                "title": f"{args.title_prefix}{probe_id}",
            },
        )
        session_id = session["id"]
        entry = {
            "id": probe_id,
            "title": title,
            "session_id": session_id,
            "expected": probe.get("expected"),
            "turns": [],
        }
        print(f"== [{probe_id}] {title} session={session_id}", flush=True)
        for idx, message in enumerate(probe["turns"]):
            print(f"   turn {idx + 1}: {message}", flush=True)
            try:
                turn = stream_turn(base_url, token, session_id, agent_id, message, args.turn_timeout)
            except urllib.error.URLError as exc:
                turn = {"error": {"detail": str(exc)}, "reply": "", "tool_calls": [], "events": []}
            turn["message"] = message
            entry["turns"].append(turn)
            print(
                f"   -> {turn.get('wall_ms')}ms tools={[t['name'] for t in turn.get('tool_calls', [])]}"
                f" reply={ (turn.get('reply') or '')[:120]!r}",
                flush=True,
            )
        entry["telemetry"] = session_telemetry(base_url, token, session_id, tenant)
        results.append(entry)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "base_url": base_url,
                "agent_id": agent_id,
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "probes": results,
            },
            fh,
            ensure_ascii=False,
            indent=1,
        )
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
