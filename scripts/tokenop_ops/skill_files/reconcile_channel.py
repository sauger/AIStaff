#!/usr/bin/env python3
"""Deterministic three-way reconciliation for TokenOp channel bills.

This script never calls the network and never writes files.  The model passes
the raw numbers already returned by the read-only tools; every sum, unit
conversion and tolerance check happens here so the transcript contains no
mental arithmetic.

Three columns are always kept separate:

    platform customer fee  |  TokenOp channel cost  |  channel bill
    CSV 消费金额(元)        |  cost_fen ÷ 100        |  quota × calibrated factor

Only the last two are subtracted (channel bill - TokenOp channel cost).  The
customer fee is never added to either side.  When the channel data is missing
or the unit factor is not calibrated, the output is a half statement and the
channel amount is not estimated.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Calibrated by `configure_ops_assets.py --calibrate-channel` and asserted
# against the asset script's constant by the backend test.  new-api display
# currency: 500000 quota = 1 unit (CNY here, usd_exchange_rate=1).
DEFAULT_CHANNEL_FACTOR: float | None = 1.0 / 500000.0

BUSINESS_TOLERANCE_YUAN = 0.01
BUSINESS_TOLERANCE_RATIO = 0.001
HALF_CENT_YUAN = 0.005

_UNKNOWN = {"", "-", "n/a", "na", "null", "none"}


@dataclass
class OrgRow:
    org_id: str
    cost_fen: int = 0
    missing: int = 0
    details: int = 0
    customer_fee_yuan: float = 0.0
    customer_fee_known: bool = True
    details_known: bool = True


@dataclass
class ChannelRow:
    quota: int = 0
    count: int = 0
    refunds: int = 0
    source: str = "api"
    status: str = "ok"
    reason: str = ""
    display_yuan: float | None = None


@dataclass
class Report:
    period: str
    scope: str
    orgs: list[OrgRow] = field(default_factory=list)
    channel: ChannelRow = field(default_factory=ChannelRow)
    factor: float | None = None
    lines: list[str] = field(default_factory=list)


def _parse_float(value: str) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("¥", "").replace("￥", "")
    if text.lower() in _UNKNOWN:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _parse_int(value: str) -> int | None:
    number = _parse_float(value)
    return None if number is None else int(number)


def parse_org_arg(raw: str) -> OrgRow:
    """Parse ``id:cost_fen:missing:details:customer_fee_yuan``.

    ``-`` marks an unknown value.  The organization id is required.
    """
    parts = str(raw or "").split(":")
    if len(parts) != 5 or not parts[0].strip():
        raise ValueError(
            f"--org 需要 5 段 id:cost_fen:missing:details:customer_fee_yuan，收到 {raw!r}"
        )
    org_id = parts[0].strip()
    cost_fen = _parse_int(parts[1])
    missing = _parse_int(parts[2])
    details = _parse_int(parts[3])
    fee = _parse_float(parts[4])
    row = OrgRow(
        org_id=org_id,
        cost_fen=cost_fen or 0,
        missing=missing or 0,
        details=details or 0,
        customer_fee_yuan=fee or 0.0,
        customer_fee_known=fee is not None,
        details_known=details is not None,
    )
    return row


def parse_ndjson(path: str) -> ChannelRow:
    """Sum raw ``quota`` of consume logs from a new-api NDJSON export."""
    quota = 0
    count = 0
    refunds = 0
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ValueError(f"读取 --channel-ndjson 失败：{exc}") from exc
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        try:
            item = json.loads(clean)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or "quota" not in item:
            continue
        log_type = item.get("type")
        if log_type == 2 or log_type == "2":
            quota += int(item.get("quota") or 0)
            count += 1
        elif log_type == 6 or log_type == "6":
            refunds += 1
    return ChannelRow(quota=quota, count=count, refunds=refunds, source="ndjson")


_COST_HEADERS = ("cost", "成本", "消费")


def parse_csv(path: str) -> ChannelRow:
    """Best-effort parse of the portal CSV export.

    The portal's Cost column is a *formatted display currency* string, not raw
    quota.  It is accepted only as a cross-check and is labelled as such.
    """
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            headers = list(reader.fieldnames or [])
    except OSError as exc:
        raise ValueError(f"读取 --channel-csv 失败：{exc}") from exc
    column = next(
        (
            header
            for header in headers
            if any(key in str(header).lower() for key in _COST_HEADERS)
            and "消费金额" not in str(header)
        ),
        None,
    )
    if column is None:
        raise ValueError(f"CSV 中未找到渠道金额列；表头={headers[:12]}")
    total = 0.0
    count = 0
    for row in rows:
        value = _parse_float(row.get(column, ""))
        if value is None:
            continue
        total += value
        count += 1
    return ChannelRow(
        quota=0,
        count=count,
        source="csv_display",
        status="ok_display",
        reason=f"显示币列「{column}」，仅交叉校验",
        display_yuan=total,
    )


def _from_index(period: str) -> list[OrgRow]:
    """Optionally reuse summarize_ops.py helpers to read persisted tool results."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from summarize_ops import (
            _bill_rows,
            _csv_rows,
            _index_entries,
            _workspace_root,
        )
    except Exception as exc:
        raise ValueError(f"--from-index 不可用（无法导入 summarize_ops）：{exc}") from exc

    root = _workspace_root()
    entries = _index_entries(root)
    bills: list[dict[str, Any]] = []
    csvs: list[dict[str, Any]] = []
    for entry in entries:
        name = str(entry.get("tool_name") or "")
        if "get_platform_consumption_bill" in name:
            bills.extend(_bill_rows(entry, root, set()))
        elif "export_platform_usage_details" in name:
            csvs.extend(_csv_rows(entry, root, set()))
    merged: dict[str, OrgRow] = {}
    for row in bills:
        if period and row.get("period") and row["period"] != period:
            continue
        org_id = str(row.get("organization_id") or "").strip()
        if not org_id:
            continue
        item = merged.setdefault(org_id, OrgRow(org_id=org_id))
        item.cost_fen = int(row.get("cost_fen") or 0)
        item.missing = int(row.get("missing_cost_rows") or 0)
        item.details = int(row.get("rows") or 0)
    for row in csvs:
        org_id = str(row.get("organization_id") or "").strip()
        if not org_id:
            continue
        item = merged.setdefault(org_id, OrgRow(org_id=org_id))
        item.customer_fee_yuan = float(row.get("customer_fee_yuan") or 0.0)
        item.customer_fee_known = True
    return list(merged.values())


def _fmt_yuan(value: float) -> str:
    return f"¥{value:,.6f}"


def _fmt_signed(value: float) -> str:
    return f"{'+' if value >= 0 else '−'}¥{abs(value):,.6f}"


def build_report(args: argparse.Namespace) -> Report:
    report = Report(period=str(args.period or "").strip(), scope=args.scope)
    if args.from_index:
        report.orgs = _from_index(report.period)
        if not report.orgs:
            raise ValueError("--from-index 未找到可汇总的账单/CSV；请改用 --org 显式传入")
    else:
        report.orgs = [parse_org_arg(raw) for raw in (args.org or [])]
    if not report.orgs and not args.from_index:
        raise ValueError("至少需要一个 --org（或 --from-index）")

    channel = ChannelRow()
    if args.channel_status and args.channel_status != "ok":
        channel.status = "missing"
        channel.reason = args.channel_status.split(":", 1)[-1] or "渠道数据不可用"
    elif args.channel_ndjson:
        channel = parse_ndjson(args.channel_ndjson)
    elif args.channel_csv:
        channel = parse_csv(args.channel_csv)
    elif args.channel_quota is not None:
        channel.quota = int(args.channel_quota)
        channel.count = 1 if channel.quota else 0
    else:
        channel.status = "missing"
        channel.reason = "未提供渠道汇总（渠道工具失败或未取数）"
    report.channel = channel
    report.factor = args.channel_factor
    _render(report)
    return report


def _verdict(report: Report) -> tuple[str, float | None, float | None, float | None, list[str]]:
    notes: list[str] = []
    orgs = report.orgs
    cost_fen = sum(item.cost_fen for item in orgs)
    missing = sum(item.missing for item in orgs)
    details = sum(item.details for item in orgs) if all(item.details_known for item in orgs) else None
    cost_yuan = cost_fen / 100.0
    if report.channel.status == "missing":
        return "半份", cost_yuan, None, None, ["渠道方账单缺失，不推算"]
    if report.scope == "enterprise":
        return "半份", cost_yuan, None, None, [
            "渠道方账单是账户级，不能与单企业成本相减；不出差异"
        ]
    if report.factor is None:
        return "半份", cost_yuan, None, None, ["渠道单位未校准，禁止换算"]
    if report.channel.source == "csv_display":
        if report.channel.display_yuan is None:
            return "半份", cost_yuan, None, None, ["CSV 未解析出渠道金额"]
        if abs(report.factor - 1.0 / 500000.0) > 1e-12:
            return "半份", cost_yuan, None, None, [
                "CSV 金额是显示币（H1）；当前校准不是 H1，需原始 quota/NDJSON"
            ]
        channel_yuan = report.channel.display_yuan
    else:
        channel_yuan = report.channel.quota * report.factor
    diff = channel_yuan - cost_yuan
    business_tol = max(BUSINESS_TOLERANCE_YUAN, abs(channel_yuan) * BUSINESS_TOLERANCE_RATIO)
    costed_rows = max(0, (details or 0) - missing) if details is not None else None
    rounding = HALF_CENT_YUAN * costed_rows if costed_rows is not None else None
    if report.channel.source == "csv_display":
        notes.append("CSV 金额是显示币格式化文本，仅作交叉校验")
    if missing > 0:
        notes.append(f"TokenOp 渠道成本缺 {missing} 行，不判平")
        return "不可判平", cost_yuan, channel_yuan, diff, notes
    if report.channel.source != "csv_display" and report.channel.quota == 0:
        notes.append("渠道方为 0 行（如实报 0）；平台有成本，核查账号/时间窗")
        return "差异", cost_yuan, channel_yuan, diff, notes
    if abs(diff) <= business_tol:
        return "平", cost_yuan, channel_yuan, diff, notes
    if rounding is not None and abs(diff) <= rounding:
        notes.append("差异在分位取整不确定度内")
        return "平（分位取整内）", cost_yuan, channel_yuan, diff, notes
    notes.append("差异超出业务容差与分位取整上界，逐项核查时间边界/退款/共享账号")
    return "差异", cost_yuan, channel_yuan, diff, notes


def _render(report: Report) -> None:
    verdict, cost_yuan, channel_yuan, diff, notes = _verdict(report)
    orgs = report.orgs
    cost_fen = sum(item.cost_fen for item in orgs)
    missing = sum(item.missing for item in orgs)
    details_known = all(item.details_known for item in orgs)
    details = sum(item.details for item in orgs) if details_known else None
    fee_known = [item for item in orgs if item.customer_fee_known]
    fee_unknown = len(orgs) - len(fee_known)
    customer_fee = sum(item.customer_fee_yuan for item in fee_known)
    costed_rows = max(0, (details or 0) - missing) if details is not None else None

    print(f"三方对账单 | 期间 {report.period or '-'} | 范围 {report.scope}")
    print(f"企业数 {len(orgs)}；有成本行 {costed_rows if costed_rows is not None else '未知'}；"
          f"缺成本行 {missing}")
    fee_note = f"{len(fee_known)} 家已知" + (f"；{fee_unknown} 家未知" if fee_unknown else "")
    print(f"平台客户费用 | {_fmt_yuan(customer_fee)} | CSV 消费金额(元)，{fee_note}")
    print(
        f"TokenOp 渠道成本 | {_fmt_yuan(cost_yuan)} | cost_fen={cost_fen} 分 ÷100"
        + (f"；缺 {missing} 行 → 不完整" if missing else "")
    )
    if report.channel.status == "missing":
        print(f"渠道方账单 | 缺失（{report.channel.reason}） | 未推算")
        print("差异（渠道方账单 − TokenOp 渠道成本） | 无法计算")
        print("判定 | 半份 | 渠道数据缺失，禁止估算")
    elif report.scope == "enterprise":
        print("渠道方账单 | 账户级（不按企业拆分） | 不参与单企业差异")
        print("差异（渠道方账单 − TokenOp 渠道成本） | 无法计算")
        print("判定 | 半份 | 渠道账单账户级，禁止分摊/推算")
    elif report.factor is None:
        print(f"渠道方账单 | 待校准（quota={report.channel.quota}） | 单位未校准，不换算")
        print("差异（渠道方账单 − TokenOp 渠道成本） | 无法计算")
        print("判定 | 半份 | 单位未校准")
    elif verdict == "半份":
        print("渠道方账单 | 不可比（见说明） | 不换算、不出差异")
        print("差异（渠道方账单 − TokenOp 渠道成本） | 无法计算")
        print("判定 | 半份 | 渠道口径不可比")
    else:
        if report.channel.source == "csv_display":
            print(f"渠道方账单 | {_fmt_yuan(channel_yuan or 0)} | CSV 显示币列合计（仅交叉校验）")
        else:
            print(f"渠道方账单 | {_fmt_yuan(channel_yuan or 0)}"
                  f" | quota={report.channel.quota} × {report.factor:.12g}"
                  + (f"；{report.channel.reason}" if report.channel.reason else ""))
        print(f"差异（渠道方账单 − TokenOp 渠道成本） | {_fmt_signed(diff or 0)}")
        business_tol = max(BUSINESS_TOLERANCE_YUAN, abs(channel_yuan or 0) * BUSINESS_TOLERANCE_RATIO)
        print(f"业务容差 | ±{_fmt_yuan(business_tol)} | max(0.01, 0.1%×渠道账单)")
        if costed_rows is not None:
            print(f"分位取整不确定度 | ±{_fmt_yuan(HALF_CENT_YUAN * costed_rows)}"
                  f" | 0.005×{costed_rows} 行")
        print(f"判定 | {verdict}")
    if notes:
        print("数据缺口/说明 | " + "；".join(notes))
    print("来源 | 平台消费账单 + 渠道 /api/log/self/stat（或导出文件）；渠道数据非发票")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--period", default="", help="账期 YYYY-MM")
    parser.add_argument("--scope", choices=["platform", "enterprise"], default="platform")
    parser.add_argument(
        "--org",
        action="append",
        default=[],
        help="id:cost_fen:missing:details:customer_fee_yuan（- 表示未知，可重复）",
    )
    parser.add_argument("--from-index", action="store_true", help="从已持久化的工具结果汇总")
    parser.add_argument("--channel-quota", type=int, default=None, help="渠道汇总 quota 原始整数")
    parser.add_argument("--channel-ndjson", default="", help="渠道 NDJSON 导出文件")
    parser.add_argument("--channel-csv", default="", help="渠道 CSV 导出文件（显示币，仅交叉校验）")
    parser.add_argument("--channel-status", default="ok", help="ok 或 missing:<原因>")
    parser.add_argument(
        "--channel-factor",
        type=float,
        default=DEFAULT_CHANNEL_FACTOR,
        help="渠道 quota→元 的校准系数；未校准不要传",
    )
    args = parser.parse_args(argv)
    if args.scope == "enterprise" and not args.org and not args.from_index:
        print("enterprise 范围需要 --org", file=sys.stderr)
        return 2
    try:
        build_report(args)
    except ValueError as exc:
        print(f"参数/数据错误：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
