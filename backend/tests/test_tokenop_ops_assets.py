from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "tokenop_ops" / "configure_ops_assets.py"
RECONCILE = ROOT / "scripts" / "tokenop_ops" / "skill_files" / "reconcile_channel.py"

_ASSET_NAMES = {
    "GATEWAY",
    "TOOL_NAMESPACE",
    "ERR_SECTION",
    "QUOTA_NOTE",
    "PERSONA",
    "SKILLS",
    "GENERAL_SKILL_ROUTES",
    "REQUIRED_SKILL_BY_TOOL",
    "HARNESS_MAX_ACTIONS",
    "CHANNEL_BASE",
    "CHANNEL_GUIDE_HOST",
    "CHANNEL_LOGIN_URL",
    "CHANNEL_UNIT_PLACEHOLDER",
    "CHANNEL_ERR_SECTION",
    "CHANNEL_YUAN_PER_QUOTA",
    "CHANNEL_UNIT_FORMULA",
    "CHANNEL_CALIBRATION_DATE",
    "CHANNEL_END_OFFSET",
}

_EXISTING_TOOL_URLS = {
    "tokenop.list_platform_organizations": "http://8.159.153.113:9070/api/platform/organizations",
    "tokenop.get_organization_account": (
        "http://8.159.153.113:9070/api/platform/organizations/{org_id}/account"
    ),
    "tokenop.get_platform_consumption_bill": (
        "http://8.159.153.113:9070/api/platform/organizations/{org_id}/consumption-bills"
    ),
    "tokenop.list_platform_credit_bills": "http://8.159.153.113:9070/api/platform/credit-bills",
    "tokenop.export_platform_usage_details": (
        "http://8.159.153.113:9070/api/platform/usage-details/export"
    ),
    "tokenop.list_platform_model_costs": (
        "http://8.159.153.113:9070/api/platform/pricing/model-costs"
    ),
    "tokenop.list_platform_usage_details": "http://8.159.153.113:9070/api/platform/usage-details",
    "tokenop.get_organization_ledger": (
        "http://8.159.153.113:9070/api/platform/organizations/{org_id}/ledger"
    ),
}

_CALIBRATED_NOTE = "new-api quota；元 = quota ÷ 500000（2026-11-04 校准，不是分）"


def _load_assets() -> dict[str, Any]:
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    namespace: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = (
                node.target
                if isinstance(node, ast.AnnAssign)
                else node.targets[0]
                if len(node.targets) == 1
                else None
            )
            if not isinstance(target, ast.Name):
                continue
            if target.id in _ASSET_NAMES:
                try:
                    namespace[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass

    def prop(schema_type: str, description: str, **_extra: Any) -> dict[str, Any]:
        return {"type": schema_type, "description": description}

    namespace["_prop"] = prop
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TOOLS":
            namespace["TOOLS"] = eval(
                compile(ast.Expression(node.value), "<tokenop-tools>", "eval"),
                {"__builtins__": {}},
                namespace,
            )
    return namespace


def _render(value: object, note: str = _CALIBRATED_NOTE) -> object:
    if isinstance(value, str):
        return value.replace("[[CHANNEL_UNIT_NOTE]]", note)
    if isinstance(value, list):
        return [_render(item, note) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, note) for key, item in value.items()}
    return value


def _load_module():
    spec = importlib.util.spec_from_file_location("tokenop_ops_assets", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tokenop_usage_detail_requires_organization_id() -> None:
    assets = _load_assets()
    assert assets["TOOLS"]["tokenop.list_platform_usage_details"]["input_schema"][
        "required"
    ] == ["organization_id"]


def test_tokenop_tools_expose_guardrails_before_fold() -> None:
    assets = _load_assets()
    tools = assets["TOOLS"]
    assert "organization_id 必填" in tools["tokenop.export_platform_usage_details"][
        "description"
    ]
    assert "organization_id 必填" in tools["tokenop.list_platform_usage_details"][
        "description"
    ]
    assert "cost_fen" in tools["tokenop.get_platform_consumption_bill"]["description"]
    assert "多候选" in tools["tokenop.list_platform_organizations"]["description"]


def test_existing_eight_tool_contracts_unchanged() -> None:
    assets = _load_assets()
    tools = assets["TOOLS"]
    assert {name: tools[name]["url"] for name in _EXISTING_TOOL_URLS} == _EXISTING_TOOL_URLS
    assert "organization_id" in tools["tokenop.export_platform_usage_details"][
        "input_schema"
    ]["required"]
    assert "organization_id" in tools["tokenop.list_platform_usage_details"][
        "input_schema"
    ]["required"]
    assert "period_ym" in tools["tokenop.get_platform_consumption_bill"]["input_schema"][
        "required"
    ]


def test_channel_tools_are_wired_and_within_budget() -> None:
    assets = _load_assets()
    tools = assets["TOOLS"]
    assert len(tools) == 10
    for name in ("tokenop.get_channel_bill", "tokenop.list_channel_bill_details"):
        spec = tools[name]
        assert spec["login_guide"] == "channel"
        assert spec["input_schema"]["required"] == ["start_timestamp", "end_timestamp"]
        assert assets["REQUIRED_SKILL_BY_TOOL"][name] == "tokenop-channel-recon"
        description = str(_render(spec["description"]))
        schema = json.dumps(
            _render(spec["input_schema"]), ensure_ascii=False, separators=(",", ":")
        )
        assert len(description) + len(schema) <= 800, name
        assert "quota" in description
        assert "不是分" in description
    bill = tools["tokenop.get_channel_bill"]
    assert "/api/log/self/stat?type=2" in bill["url"]
    assert "tk.lanxiongai.com" in bill["url"]


def test_tokenop_skills_and_routes_are_wired() -> None:
    assets = _load_assets()
    skills = assets["SKILLS"]
    assert set(skills) == {
        "tokenop-customer-billing",
        "tokenop-channel-cost-billing",
        "tokenop-patrol-balance-alert",
        "tokenop-ops-analysis-report",
        "tokenop-channel-recon",
    }
    for slug, spec in skills.items():
        markdown = str(_render(spec["markdown"]))
        assert "## 前置拒绝" in markdown, slug
        assert "## 技能前置" in markdown, slug
        assert "## 槽位" in markdown, slug
        assert len(markdown) <= 2000, slug
    for slug in (
        "tokenop-customer-billing",
        "tokenop-channel-cost-billing",
        "tokenop-ops-analysis-report",
    ):
        assert "summarize_ops.py" in skills[slug]["markdown"], slug
    recon = skills["tokenop-channel-recon"]
    assert "reconcile_channel.py" in recon["markdown"]
    assert "## 判定与降级" in recon["markdown"]
    assert recon["scripts"] == ["summarize_ops.py", "reconcile_channel.py"]
    assert RECONCILE.is_file()

    routes = assets["GENERAL_SKILL_ROUTES"]
    assert any(route.get("mode") == "refuse" for route in routes)
    by_id = {route["id"]: route for route in routes}
    assert by_id["channel-recon"]["priority"] > by_id["channel-cost-billing"]["priority"]
    assert by_id["channel-recon"]["skills"] == ["tokenop-channel-recon"]
    assert by_id["refuse-third-party-sum"]["priority"] == 99
    assert by_id["refuse-third-party-sum"]["mode"] == "refuse"
    assert (
        assets["REQUIRED_SKILL_BY_TOOL"]["tokenop.get_organization_account"]
        == "tokenop-patrol-balance-alert"
    )
    assert assets["HARNESS_MAX_ACTIONS"] == 20
    assert (ROOT / "scripts" / "tokenop_ops" / "skill_files" / "summarize_ops.py").is_file()


def test_pick_channel_guide_by_url() -> None:
    module = _load_module()
    web = {
        "name": "岚熊",
        "url": "https://tk.lanxiongai.com/",
        "guide_type": "web",
    }
    api = {
        "name": "岚熊渠道后台",
        "url": "https://tk.lanxiongai.com/api/user/login",
        "guide_type": "api",
    }
    assert module.pick_channel_guide([web]) is None
    assert module.pick_channel_guide([web, api]) == api
    with pytest.raises(RuntimeError):
        module.pick_channel_guide(
            [
                api,
                {
                    "name": "岚熊备用",
                    "url": "https://tk.lanxiongai.com/api/user/login",
                    "guide_type": "api",
                },
            ]
        )
