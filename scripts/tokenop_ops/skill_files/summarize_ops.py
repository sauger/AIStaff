#!/usr/bin/env python3
"""Summarize TokenOp export/bill results already written into this workspace.

This script is shipped inside the TokenOp GeneralSkill packages and is invoked
through the typed ``run_skill_script`` tool.  It never calls the network and
never invents data: it only reads result files that the Harness already
persisted, using ``.harness/tool-results/index.jsonl`` when available and
falling back to ``exports/*.csv`` / ``.harness/tool-results/*.json``.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import sys
from pathlib import Path
from typing import Any


def _workspace_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".harness").is_dir() or (parent / "exports").is_dir():
            return parent
    return Path.cwd()


def _load_index(root: Path) -> list[dict[str, Any]]:
    path = root / ".harness" / "tool-results" / "index.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            entries.append(value)
    return entries


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError:
        return []


def _number(value: object) -> float:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _org_filter(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _index_entries(root: Path) -> list[dict[str, Any]]:
    entries = _load_index(root)
    if entries:
        return entries
    fallback: list[dict[str, Any]] = []
    for path in glob.glob(str(root / "exports" / "*.csv")):
        fallback.append({"path": path, "tool_name": "tokenop.export_platform_usage_details"})
    for path in glob.glob(str(root / ".harness" / "tool-results" / "*.json")):
        fallback.append({"path": path, "tool_name": "tokenop.get_platform_consumption_bill", "kind": "json"})
    return fallback


def _entry_path(root: Path, entry: dict[str, Any]) -> Path | None:
    raw = str(entry.get("path") or entry.get("sandbox_path") or "").strip()
    if not raw:
        return None
    if raw.startswith("/workspace/"):
        raw = raw.removeprefix("/workspace/")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path if path.is_file() else None


def _entry_org(entry: dict[str, Any]) -> str:
    args = entry.get("arguments")
    if not isinstance(args, dict):
        return ""
    return str(args.get("organization_id") or args.get("org_id") or "").strip()


def _entry_period(entry: dict[str, Any]) -> str:
    args = entry.get("arguments")
    if not isinstance(args, dict):
        return ""
    return str(args.get("period_ym") or "").strip()


def _csv_rows(entry: dict[str, Any], root: Path, orgs: set[str]) -> list[dict[str, Any]]:
    path = _entry_path(root, entry)
    if path is None:
        return []
    org_id = _entry_org(entry)
    if orgs and org_id and org_id not in orgs:
        return []
    rows = _read_csv(path)
    if not rows:
        return []
    name = ""
    tokens = 0.0
    fee = 0.0
    count = 0
    for row in rows:
        name = name or str(row.get("企业") or "").strip()
        tokens += _number(row.get("总 Token"))
        fee += _number(row.get("消费金额(元)"))
        count += 1
    return [
        {
            "organization_id": org_id,
            "organization": name,
            "customer_fee_yuan": fee,
            "tokens": int(tokens),
            "rows": count,
            "cost_fen": None,
            "missing_cost_rows": None,
            "period": _entry_period(entry),
            "source": path.name,
        }
    ]


def _bill_rows(entry: dict[str, Any], root: Path, orgs: set[str]) -> list[dict[str, Any]]:
    path = _entry_path(root, entry)
    if path is None:
        return []
    org_id = _entry_org(entry)
    if orgs and org_id and org_id not in orgs:
        return []
    payload = _read_json(path)
    data = payload.get("data") if isinstance(payload, dict) and "data" in payload else payload
    if not isinstance(data, dict):
        return []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    cost_fen = summary.get("cost_fen")
    missing = summary.get("missing_cost_count")
    return [
        {
            "organization_id": org_id,
            "organization": str(data.get("organization_name") or data.get("organization_id") or ""),
            "customer_fee_yuan": None,
            "tokens": int(_number(summary.get("token_count"))),
            "rows": int(_number(summary.get("details_total"))),
            "cost_fen": int(_number(cost_fen)) if cost_fen is not None else None,
            "missing_cost_rows": int(_number(missing)) if missing is not None else None,
            "period": _entry_period(entry) or str(data.get("period_ym") or ""),
            "source": path.name,
        }
    ]


def _merge_duplicates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("organization_id") or ""),
            str(row.get("period") or ""),
            str(row.get("organization") or ""),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            continue
        for field in ("customer_fee_yuan", "tokens", "rows"):
            if row.get(field) is not None:
                existing[field] = row[field]
        for field in ("cost_fen", "missing_cost_rows", "period"):
            if row.get(field) is not None:
                existing[field] = row[field]
        existing["source"] = f"{existing.get('source')},{row.get('source')}"
    return list(merged.values())


def _print_table(rows: list[dict[str, Any]], mode: str, max_rows: int) -> None:
    print("企业ID\t客户费用元\tToken\t成本分\tmissing行数\t期间\t来源")
    for row in rows[: max(1, max_rows)]:
        print(
            "\t".join(
                [
                    str(row.get("organization_id") or ""),
                    (
                        f"{float(row['customer_fee_yuan']):.6f}"
                        if row.get("customer_fee_yuan") is not None
                        else "-"
                    ),
                    str(row.get("tokens") if row.get("tokens") is not None else "-"),
                    str(row.get("cost_fen") if row.get("cost_fen") is not None else "-"),
                    str(
                        row.get("missing_cost_rows")
                        if row.get("missing_cost_rows") is not None
                        else "-"
                    ),
                    str(row.get("period") or "-"),
                    str(row.get("source") or "-"),
                ]
            )
        )
    if len(rows) > max_rows:
        print(f"# truncated: {len(rows) - max_rows} more rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org-ids", default="", help="comma separated organization ids")
    parser.add_argument("--period", default="", help="YYYY-MM")
    parser.add_argument("--mode", choices=["customer", "cost", "ops"], default="ops")
    parser.add_argument("--max-rows", type=int, default=20)
    args = parser.parse_args(argv)
    root = _workspace_root()
    orgs = _org_filter(args.org_ids)
    rows: list[dict[str, Any]] = []
    for entry in _index_entries(root):
        tool_name = str(entry.get("tool_name") or "")
        if "export_platform_usage_details" in tool_name:
            rows.extend(_csv_rows(entry, root, orgs))
        elif "get_platform_consumption_bill" in tool_name:
            rows.extend(_bill_rows(entry, root, orgs))
    rows = _merge_duplicates(rows)
    if args.period:
        rows = [row for row in rows if not row.get("period") or row["period"] == args.period]
    if not rows:
        print(
            "未找到可汇总的 export/bill 结果文件。请确认取数已完成且结果为沙箱文件；"
            "内联的小结果不需要本脚本，直接用 finish 汇总。",
            file=sys.stderr,
        )
        return 2
    _print_table(rows, args.mode, args.max_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
