from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "tokenop_ops" / "skill_files" / "reconcile_channel.py"
ASSETS = ROOT / "scripts" / "tokenop_ops" / "configure_ops_assets.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _load_assets():
    spec = importlib.util.spec_from_file_location("tokenop_ops_assets_for_reconcile", ASSETS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_reconcile():
    spec = importlib.util.spec_from_file_location("tokenop_reconcile_channel", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_complete_period_is_balanced() -> None:
    result = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-quota",
        "30181",
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "¥0.060362" in out
    assert "¥0.060000" in out
    assert "判定 | 平" in out
    assert "301.81" not in out


def test_missing_cost_rows_are_not_balanced() -> None:
    result = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:4:24:0.060362",
        "--channel-quota",
        "30181",
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    assert "判定 | 不可判平" in result.stdout
    assert "缺 4 行" in result.stdout
    assert "补" not in result.stdout


def test_missing_channel_is_half_statement_without_numbers() -> None:
    result = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:4:24:0.060362",
        "--channel-status",
        "missing:401",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "判定 | 半份" in out
    assert "无法计算" in out
    assert "缺失（401）" in out
    assert "未推算" in out


def test_enterprise_scope_never_allocates_channel_bill() -> None:
    result = _run(
        "--scope",
        "enterprise",
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-quota",
        "30181",
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "账户级（不按企业拆分）" in out
    assert "判定 | 半份" in out
    assert "差异（渠道方账单 − TokenOp 渠道成本） | 无法计算" in out


def test_zero_channel_rows_are_reported_as_zero_not_missing() -> None:
    result = _run(
        "--period",
        "2026-09",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-quota",
        "0",
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "¥0.000000" in out
    assert "如实报 0" in out
    assert "缺失" not in out.split("渠道方账单", 1)[1].split("\n", 1)[0]


def test_rounding_tolerance_branch() -> None:
    # 0.02 元差异 > 0.01 业务容差，但 <= 0.005*24=0.12 分位取整上界
    result = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-quota",
        "40181",
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    assert "平（分位取整内）" in result.stdout


def test_csv_display_currency_is_only_cross_check(tmp_path: Path) -> None:
    csv_path = tmp_path / "channel.csv"
    csv_path.write_text(
        "时间,类型,模型,成本\n"
        "2026-10-01 00:00:00,消费,m,¥0.020000\n"
        "2026-10-02 00:00:00,消费,m,¥0.040362\n",
        encoding="utf-8",
    )
    h1 = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-csv",
        str(csv_path),
        "--channel-factor",
        "0.000002",
    )
    assert h1.returncode == 0, h1.stderr
    assert "CSV 显示币列合计" in h1.stdout

    h2 = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-csv",
        str(csv_path),
        "--channel-factor",
        "0.0000146",
    )
    assert h2.returncode == 0, h2.stderr
    assert "判定 | 半份" in h2.stdout
    assert "CSV 金额是显示币（H1）" in h2.stdout


def test_ndjson_sums_only_consume_and_skips_summary(tmp_path: Path) -> None:
    path = tmp_path / "channel.ndjson"
    lines = [
        {"created_at": 1, "type": 2, "quota": 10000},
        {"created_at": 2, "type": 2, "quota": 20181},
        {"created_at": 3, "type": 6, "quota": -500},
        {"success": True, "data": {"count": 3}},
    ]
    path.write_text("\n".join(json.dumps(item) for item in lines), encoding="utf-8")
    result = _run(
        "--period",
        "2026-10",
        "--org",
        "7:6:0:24:0.060362",
        "--channel-ndjson",
        str(path),
        "--channel-factor",
        "0.000002",
    )
    assert result.returncode == 0, result.stderr
    assert "quota=30181" in result.stdout


def test_uncalibrated_factor_is_half_statement() -> None:
    module = _load_reconcile()
    original = module.DEFAULT_CHANNEL_FACTOR
    module.DEFAULT_CHANNEL_FACTOR = None
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = module.main(
                [
                    "--period",
                    "2026-10",
                    "--org",
                    "7:6:0:24:0.060362",
                    "--channel-quota",
                    "30181",
                ]
            )
    finally:
        module.DEFAULT_CHANNEL_FACTOR = original
    assert rc == 0
    out = buffer.getvalue()
    assert "判定 | 半份" in out
    assert "单位未校准" in out


def test_factor_matches_asset_constant() -> None:
    assets = _load_assets()
    reconcile = _load_reconcile()
    assert reconcile.DEFAULT_CHANNEL_FACTOR == assets.CHANNEL_YUAN_PER_QUOTA
