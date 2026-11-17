#!/usr/bin/env python3
"""TokenOp 运营版 Phase 1-2 资产配置（幂等）。

通过本地 StaffDeck HTTP API 更新 TokenOp 的工具契约、人设、技能与 SOP。
不直接写数据库；不包含任何密码/Secret（凭据从环境变量读取）。

用法:
    # 预演（默认，只打印计划，不改动）
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py

    # 实际应用
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py --apply

    # 只读校验 / 导出线上快照（不做写操作）
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py --check
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py --export /tmp/tokenop.json

    # 应用后用企业 7 冒烟测试两个新工具
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py --apply --smoke-test

    # 只读校准渠道方（岚熊）quota→元 与时间边界（不写任何资产）
    STAFFDECK_TOKEN=... python3 scripts/tokenop_ops/configure_ops_assets.py --calibrate-channel --period 2026-10

渠道方（岚熊）凭证:
    - 只在 TokenOp 员工「密钥」页配置 API 型登录说明 + 密码密钥；本脚本按
      URL 含 tk.lanxiongai.com 发现并绑定，不创建/修改任何凭证。
    - 渠道 quota 必须先校准；未校准（CHANNEL_YUAN_PER_QUOTA=None）时只出半份，
      禁止把 quota 换算成元。

环境变量:
    STAFFDECK_URL     本地服务地址，默认 http://127.0.0.1:5173
    STAFFDECK_TENANT  租户，默认 tenant_demo
    TOKENOP_AGENT_ID  默认 agent_aa14eea9e0a143af
    STAFFDECK_TOKEN   Bearer token；或 STAFFDECK_USERNAME/STAFFDECK_PASSWORD 现场登录

金额单位口径（依据 rpdnex test-v1.4.5 源码与测试网关实测）:
    - 客户 CSV「消费金额(元)」已是元。
    - 消费账单 cost_fen 是分；missing_cost 不得当 0；missing_cost_amount_fen 不作为成本。
    - 账户 balance/credit_*、台账 delta/after、消费账单 amount_fen、用量明细 amount_fen、
      授信账单 amount_fen：均为 new-api quota（字段名带 _fen 是历史遗留，网关 v1.1.1
      起统一按 quota 存储），报人民币 ÷500000，禁止 ÷100。
    - 客户费用 / 渠道成本 / 余额 / 授信 四套数不得相加。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

GATEWAY = "http://8.159.153.113:9070"
LOGIN_GUIDE_NAME = "网关测试环境"

# 渠道方（岚熊）只读账单。凭证不在这里创建/修改：统一在 TokenOp 员工「密钥」页
# 配置 API 型登录说明 + 密码密钥，本脚本只按 URL 发现并绑定。
CHANNEL_BASE = "https://tk.lanxiongai.com"
CHANNEL_GUIDE_HOST = "tk.lanxiongai.com"
CHANNEL_LOGIN_URL = f"{CHANNEL_BASE}/api/user/login"
CHANNEL_UNIT_PLACEHOLDER = "[[CHANNEL_UNIT_NOTE]]"
# 校准：new-api 显示币实证（quota_per_unit=500000、display=CNY、usd_exchange_rate=1）
# 元 = quota ÷ 500000。price=7.3 是充值价，不是账单显示币；H2 仅作采购成本候选。
CHANNEL_YUAN_PER_QUOTA: float | None = 1.0 / 500000.0
CHANNEL_UNIT_FORMULA = "quota ÷ 500000"
CHANNEL_CALIBRATION_DATE = "2026-11-04"
CHANNEL_END_OFFSET = 1  # end_timestamp = 平台账单 to - offset；日窗口 = 次日 00:00 - 1
TOOL_NAMESPACE = os.environ.get("TOKENOP_TOOL_NAMESPACE", "").strip().strip(".")

ERR_SECTION = (
    "错误处理：网关返回 success=false 时 message 带原因，原样转述不得猜测；"
    "HTTP 401=登录失效（登录说明会自动重登一次，仍失败立即停止并请管理员检查「网关测试环境」）；"
    "403=权限不足，停止；网络错误/超时重试一次后仍失败则报告，不得编造数据。"
)

QUOTA_NOTE = "new-api quota，报人民币 ÷500000（字段名带 fen 为历史遗留，不是分）"
CHANNEL_ERR_SECTION = (
    "错误处理：渠道返回 success=false 时 message 带原因，原样转述不得猜测；"
    "HTTP 401=渠道登录失效（登录说明会自动重登一次，仍失败立即停止并出半份对账单）；"
    "403/429/网络错误/超时：停止，不推算渠道数字。"
)
HARNESS_MAX_ACTIONS = 20


def channel_unit_note() -> str:
    """Human-readable channel quota unit, or an uncalibrated gate."""
    if CHANNEL_YUAN_PER_QUOTA is None or not CHANNEL_UNIT_FORMULA:
        return "new-api quota；单位未校准：禁止换算成元，渠道账单只出「待校准」"
    date = f"{CHANNEL_CALIBRATION_DATE} 校准，" if CHANNEL_CALIBRATION_DATE else ""
    return f"new-api quota；元 = {CHANNEL_UNIT_FORMULA}（{date}不是分）"
# Only tools owned by a single skill are hard-gated.  Export/usage/bill/list
# are shared by several skills, so their skill preflight is handled by the
# route/prompt rather than a single required slug.
REQUIRED_SKILL_BY_TOOL = {
    "tokenop.get_organization_account": "tokenop-patrol-balance-alert",
    "tokenop.list_platform_credit_bills": "tokenop-patrol-balance-alert",
    "tokenop.get_organization_ledger": "tokenop-ops-analysis-report",
    "tokenop.get_channel_bill": "tokenop-channel-recon",
    "tokenop.list_channel_bill_details": "tokenop-channel-recon",
}
GENERAL_SKILL_ROUTES = [
    {
        "id": "refuse-third-party-sum",
        "priority": 99,
        "mode": "refuse",
        "patterns": [
            r"渠道方账单.{0,24}(加进|加上|加到|并入|合计|合并|相加).{0,24}(客户费用|收入|总)",
            r"(客户费用|消费金额).{0,24}(加|加上|并入|合计|合并).{0,24}渠道方账单",
        ],
        "skills": ["tokenop-channel-recon"],
        "reason": "渠道方账单只用于与渠道成本相减求差异，禁止与客户费用相加或算收入/毛利",
    },
    {
        "id": "channel-recon",
        "priority": 85,
        "patterns": [
            r"三方",
            r"对账",
            r"渠道方账单",
            r"渠道账单",
            r"岚熊",
            r"上游账单",
            r"渠道后台",
        ],
        "skills": ["tokenop-channel-recon"],
    },
    {
        "id": "refuse-cross-caliber",
        "priority": 100,
        "mode": "refuse",
        "patterns": [
            r"(客户费用|消费金额|客户账单).{0,60}(渠道成本|成本).{0,20}(加|合计|一共|总|合并)",
            r"(加|加上|合并).{0,20}(渠道成本|成本).{0,40}(合计|一共|总|多少)",
            r"毛利",
        ],
        "skills": ["tokenop-ops-analysis-report"],
        "reason": "客户费用与渠道成本属于不同口径，禁止相加或计算毛利",
    },
    {
        "id": "refuse-write",
        "priority": 95,
        "mode": "refuse",
        "patterns": [r"充值", r"调账", r"改价", r"打折", r"发.{0,12}邮箱", r"发送.{0,12}账单"],
        "skills": ["tokenop-ops-analysis-report"],
        "reason": "当前 TokenOp 没有写工具，高风险操作必须人工处理",
    },
    {
        "id": "customer-billing",
        "priority": 80,
        "patterns": [r"客户费用", r"客户账单", r"客户用量", r"用量明细", r"消费金额"],
        "skills": ["tokenop-customer-billing"],
    },
    {
        "id": "channel-cost-billing",
        "priority": 70,
        "patterns": [r"渠道成本", r"消费账单", r"cost_fen"],
        "skills": ["tokenop-channel-cost-billing"],
    },
    {
        "id": "patrol-balance",
        "priority": 60,
        "patterns": [r"余额", r"授信", r"巡检", r"低于.{0,8}元"],
        "skills": ["tokenop-patrol-balance-alert"],
    },
    {
        "id": "ops-report",
        "priority": 65,
        "patterns": [r"运营", r"四套数", r"小结", r"跨数据集", r"分析报告"],
        "skills": ["tokenop-ops-analysis-report"],
    },
]


def _prop(schema_type: str, desc: str, **extra) -> dict:
    node = {"type": schema_type, "description": desc}
    node.update(extra)
    return node


# ---------------------------------------------------------------------------
# 工具契约：6 个更新 + 2 个新增
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict] = {
    "tokenop.list_platform_organizations": {
        "display_name": "平台企业列表",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/organizations",
        "description": (
            "按名称关键词把企业名解析成数字 ID；只读，多候选必须让用户选，余额只是快照。"
            "keyword 是子串匹配，禁止擅自认定；已有 org_id 时不要再搜。查账户用 "
            "get_organization_account，查消费用用量明细或消费账单。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": _prop("string", "企业名关键词，子串匹配，可空"),
                "status": _prop("string", "trial|active|suspended，可空"),
                "page": _prop("integer", "页码，从 1 开始"),
                "page_size": _prop("integer", "每页条数，默认 20"),
            },
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "list": {
                            "type": "array",
                            "description": "企业列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": _prop("integer", "企业数字 ID，后续工具的 org_id/organization_id", example=7),
                                    "name": _prop("string", "企业名称", example="小雨测试企业1"),
                                    "status": _prop("string", "trial|active|suspended"),
                                    "corridor_id": _prop("integer", "所属走廊/站点 ID"),
                                    "contact_name": _prop("string", "联系人，可空"),
                                    "balance": _prop("integer", "现金余额，" + QUOTA_NOTE, example=94979104),
                                    "credit_limit": _prop("integer", "授信额度，" + QUOTA_NOTE + "；0=未开通授信"),
                                    "credit_used": _prop("integer", "授信已用，" + QUOTA_NOTE),
                                    "balance_warning_threshold_fen": _prop("integer", "平台内置余额预警阈值，与 balance 同为 new-api quota；报人民币 ÷500000；0=未设置"),
                                    "channel_count": _prop("integer", "已绑定通道数"),
                                    "created_at": _prop("integer", "创建时间，Unix 秒"),
                                    "updated_at": _prop("integer", "更新时间，Unix 秒"),
                                },
                            },
                        },
                        "page": _prop("integer", "当前页码"),
                        "page_size": _prop("integer", "每页条数"),
                        "total": _prop("integer", "命中总数，翻页依据"),
                    },
                },
            },
        },
    },
    "tokenop.get_organization_account": {
        "display_name": "企业账户",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/organizations/{{org_id}}/account",
        "description": (
            "按企业 ID 读账户：现金余额、授信额度/已用/剩余、可用额度、状态。全部是 new-api quota，"
            "报人民币必须 ÷500000（例：94979104 quota ≈ ¥189.96），禁止当分 ÷100。只读。\n"
            "何时不该用：它不是消费金额也不是成本——客户费用用用量明细，渠道成本用消费账单；"
            "credit_limit=0 表示未开通授信，不要当成「授信低于阈值」误报。"
            + ERR_SECTION
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": _prop("string", "企业数字 ID，写入 URL 路径", example="7"),
            },
            "required": ["org_id"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "organization_id": _prop("integer", "企业数字 ID", example=7),
                        "balance": _prop("integer", "现金余额，" + QUOTA_NOTE + "，例 94979104≈¥189.96"),
                        "credit_limit": _prop("integer", "授信额度，" + QUOTA_NOTE + "；0=未开通授信"),
                        "credit_used": _prop("integer", "授信已用，" + QUOTA_NOTE),
                        "credit_remaining": _prop("integer", "授信剩余 = credit_limit - credit_used，" + QUOTA_NOTE),
                        "available": _prop("integer", "可用额度 = balance + credit_remaining，" + QUOTA_NOTE),
                        "status": _prop("string", "企业状态 trial|active|suspended"),
                    },
                },
            },
        },
    },
    "tokenop.get_platform_consumption_bill": {
        "display_name": "平台消费账单（渠道成本）",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/organizations/{{org_id}}/consumption-bills",
        "description": (
            "渠道成本只认 cost_fen（分，报元 ÷100），平台快照不是发票，禁止与 amount_fen 相加。"
            "amount_fen 是 quota（÷500000）；profit_fen 禁止引用。missing_cost 只披露行数，"
            "不得当 0，不得把 missing_cost_amount_fen 当成本。客户费用和余额不用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": _prop("string", "企业数字 ID"),
                "period_ym": _prop("string", "账期 YYYY-MM"),
                "page": _prop("integer", "details 页码，从 1 开始"),
                "page_size": _prop("integer", "每页条数，最大 100"),
            },
            "required": ["org_id", "period_ym"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "period_ym": _prop("string", "账期 YYYY-MM"),
                        "from": _prop("integer", "账期开始，Unix 秒"),
                        "to": _prop("integer", "账期结束，Unix 秒"),
                        "summary": {
                            "type": "object",
                            "description": "全账期汇总（与分页无关）",
                            "properties": {
                                "cost_fen": _prop("integer", "渠道成本合计，单位分，报元 ÷100", example=6),
                                "missing_cost_count": _prop("integer", "缺成本的明细行数，必须披露", example=4),
                                "missing_cost_amount_fen": _prop("integer", "缺成本行客户侧 amount_fen 的累加，按客户侧 quota 口径；不是渠道成本，不得换算成成本金额"),
                                "amount_fen": _prop("integer", "客户侧消费，" + QUOTA_NOTE + "，与 cost_fen 不同单位禁止相加", example=30181),
                                "costable_amount_fen": _prop("integer", "可计成本行的客户侧金额，" + QUOTA_NOTE),
                                "profit_fen": _prop("integer", "口径混杂（quota 减分），禁止引用"),
                                "token_count": _prop("integer", "Token 合计"),
                                "request_count": _prop("integer", "请求数"),
                                "details_total": _prop("integer", "明细总行数，翻页依据", example=24),
                                "truncated": _prop("boolean", "true=结果被截断，不完整"),
                            },
                        },
                        "details": {
                            "type": "array",
                            "description": "当前页明细；按渠道汇总时须取完全部页",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "created_at": _prop("integer", "发生时间，Unix 秒"),
                                    "model_name": _prop("string", "模型名"),
                                    "user_id": _prop("integer", "成员 ID"),
                                    "user_name": _prop("string", "成员名"),
                                    "department_id": _prop("integer", "部门 ID，可缺"),
                                    "corridor_id": _prop("integer", "走廊 ID"),
                                    "type": _prop("string", "consume|refund"),
                                    "phase": _prop("string", "计费阶段 pre|adjust"),
                                    "request_id": _prop("string", "请求 ID"),
                                    "amount_fen": _prop("integer", "客户侧金额，" + QUOTA_NOTE + "，signed：退款为负", example=-1250000),
                                    "prompt_tokens": _prop("integer", "输入 Token"),
                                    "completion_tokens": _prop("integer", "输出 Token"),
                                    "cost_fen": _prop("integer", "本行渠道成本，单位分，signed；缺省=missing_cost"),
                                    "missing_cost": _prop("boolean", "true=本行缺成本，只能披露不能估"),
                                    "channel_id": _prop("integer", "服务渠道 ID；0/缺省=未记渠道"),
                                    "channel_name": _prop("string", "渠道名，missing 行可缺"),
                                    "profit_fen": _prop("integer", "口径混杂，禁止引用"),
                                },
                            },
                        },
                        "by_model": {
                            "type": "array",
                            "description": "按模型汇总，字段单位同 summary（cost_fen 分、amount_fen quota）",
                        },
                        "by_member": {"type": "array", "description": "按成员汇总（amount_fen 为 quota）"},
                        "by_department": {"type": "array", "description": "按部门汇总（amount_fen 为 quota）"},
                    },
                },
            },
        },
    },
    "tokenop.list_platform_credit_bills": {
        "display_name": "授信欠款账单",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/credit-bills",
        "description": (
            "列出授信欠款账单（按企业 × 自然月生成，status: open|paid|overdue）。"
            "amount_fen 名字带 fen 但实为 quota（网关 v1.1.1 起），报人民币 ÷500000，禁止 ÷100。"
            "授信快照 credit_*_snapshot 同为 quota。只读。\n"
            "何时不该用：不是客户消费账单、不是渠道成本、不是余额；金额不得与那三套数相加。"
            "测试网关当前可能没有任何账单，空列表是正常结果，不是错误，如实报 0 条。"
            + ERR_SECTION
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "period_ym": _prop("string", "账期 YYYY-MM，可空=不过滤", example="2026-10"),
                "status": _prop("string", "open|paid|overdue，可空"),
                "organization_id": _prop("string", "企业数字 ID，可空", example="7"),
                "org_name": _prop("string", "企业名，可空"),
                "page": _prop("integer", "页码，从 1 开始", default=1),
                "page_size": _prop("integer", "每页条数", default=20),
            },
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "list": {
                            "type": "array",
                            "description": "账单列表，可为空",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "organization_id": _prop("integer", "企业数字 ID"),
                                    "organization_name": _prop("string", "企业名"),
                                    "period_ym": _prop("string", "账期 YYYY-MM"),
                                    "amount_fen": _prop("integer", "欠款金额，" + QUOTA_NOTE),
                                    "status": _prop("string", "open|paid|overdue"),
                                    "due_at": _prop("integer", "到期时间，Unix 秒"),
                                    "credit_limit_snapshot": _prop("integer", "授信额度快照，" + QUOTA_NOTE),
                                    "credit_used_snapshot": _prop("integer", "授信已用快照，" + QUOTA_NOTE),
                                },
                            },
                        },
                        "page": _prop("integer", "当前页码"),
                        "page_size": _prop("integer", "每页条数"),
                        "total": _prop("integer", "总数，翻页依据"),
                    },
                },
            },
        },
    },
    "tokenop.export_platform_usage_details": {
        "display_name": "客户用量明细 CSV 导出",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/usage-details/export",
        "description": (
            "客户用量 CSV「消费金额(元)」已是元，organization_id 必填，禁止省略导出全平台。"
            "禁止再 ÷100 或 ×100；Token 用「总 Token」列，必须代码求和。上限 10000 行。"
            "渠道成本、余额、授信不用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from": _prop("integer", "起始日 00:00:00 的 Unix 秒"),
                "to": _prop("integer", "结束日次日 00:00:00 的 Unix 秒"),
                "organization_id": _prop("string", "必填；禁止省略导出全平台"),
                "model": {"type": "string"},
                "username": {"type": "string"},
                "request_id": {"type": "string"},
                "token_name": {"type": "string"},
            },
            "required": ["from", "to", "organization_id"],
        },
        "output_schema": {
            "type": "string",
            "content_type": "text/csv",
            "description": (
                "CSV 文本（UTF-8 带 BOM），0 行时只有表头。列：调用时间, 调用序列号, 企业, 渠道, "
                "调用用户, 调用模型, 输入 Token, 未命中输入 Token, 缓存读取 Token, 输出 Token, 总 Token, "
                "缓存写入 Token, 缓存写入金额(元), 视频时长(秒), 报价系数, 消费金额(元), 明细。"
                "金额列已是元。这是客户费用口径，与渠道成本/余额/授信不得相加。"
                "行数上限 10000，超限截断。"
            ),
        },
    },
    "tokenop.list_platform_model_costs": {
        "display_name": "模型成本价目（参考）",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/pricing/model-costs",
        "description": (
            "列出各模型在各网关渠道的成本倍率/成本价，仅作价目参考。cost_ratio 是相对官方价的倍率，"
            "cost_price=0 表示未配专价。只读、无参数。\n"
            "何时不该用：它不是账单——算实际渠道成本只能用消费账单 cost_fen；不得拿价目×token 数"
            "去「补」missing_cost 的金额。"
            + ERR_SECTION
        ),
        "input_schema": {"type": "object", "properties": {}},
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "description": "按模型分组的渠道价目",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "model": _prop("string", "模型名", example="deepseek-v4-pro"),
                                    "channels": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "channel_id": _prop("integer", "渠道 ID"),
                                                "channel_name": _prop("string", "渠道名"),
                                                "cost_price": _prop("number", "专配成本价；0=未配置"),
                                                "cost_ratio": _prop("number", "相对官方价的成本倍率", example=1),
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        "global_cost_prices": {
                            "type": "object",
                            "description": "全局兜底成本价映射，通常为空对象",
                        },
                    },
                },
            },
        },
    },
    # ---- 新增工具 ----
    "tokenop.list_platform_usage_details": {
        "display_name": "客户用量明细（JSON 分页）",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/usage-details",
        "description": (
            "客户用量 JSON 的 amount_fen 是 quota（÷500000），organization_id 必填，"
            "禁止省略拉全平台。按 Unix 秒时间窗分页；缺 from/to 会拉全量；"
            "行数多要合计时改用 export。渠道成本、余额、授信不用它。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from": _prop("integer", "缺省=不限时间，会拉全量"),
                "to": _prop("integer", "缺省=不限时间"),
                "organization_id": _prop("string", "必填；禁止省略导出全平台"),
                "model": {"type": "string"},
                "username": {"type": "string"},
                "user_id": {"type": "integer"},
                "request_id": {"type": "string"},
                "token_name": {"type": "string"},
                "page": {"type": "integer"},
                "page_size": {"type": "integer"},
            },
            "required": ["organization_id"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "list": {
                            "type": "array",
                            "description": "用量明细行，时间倒序",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "created_at": _prop("integer", "请求时间，Unix 秒"),
                                    "request_id": _prop("string", "请求 ID，核对单笔的键"),
                                    "user_id": _prop("integer", "成员 ID"),
                                    "username": _prop("string", "成员名"),
                                    "display_name": _prop("string", "成员显示名，可缺"),
                                    "model_name": _prop("string", "模型名"),
                                    "token_name": _prop("string", "令牌名，可缺"),
                                    "prompt_tokens": _prop("integer", "输入 Token"),
                                    "completion_tokens": _prop("integer", "输出 Token"),
                                    "total_tokens": _prop("integer", "总 Token"),
                                    "price_mode": _prop("string", "计价模式 fixed|per_second|tiered|unset"),
                                    "input_yuan_per_1m": _prop("number", "输入单价，元/百万 Token"),
                                    "output_yuan_per_1m": _prop("number", "输出单价，元/百万 Token"),
                                    "price_yuan": _prop("number", "本行价格快照，元"),
                                    "duration_seconds": _prop("integer", "耗时秒，可缺"),
                                    "amount_fen": _prop("integer", "净消费金额，" + QUOTA_NOTE + "，signed 退款为负", example=10020),
                                    "cache_read_amount_fen": _prop("integer", "缓存读金额，" + QUOTA_NOTE + "，可缺"),
                                    "cache_write_amount_fen": _prop("integer", "缓存写金额，" + QUOTA_NOTE + "，可缺"),
                                    "model_ratio": _prop("number", "模型倍率快照，可缺"),
                                    "group_ratio": _prop("number", "分组倍率快照，可缺"),
                                    "quote_ratio": _prop("number", "报价折扣快照，可缺"),
                                    "channel_id": _prop("integer", "服务渠道 ID，仅平台视图返回"),
                                    "channel_name": _prop("string", "渠道名，仅平台视图返回"),
                                    "type": _prop("string", "consume|refund"),
                                    "phase": _prop("string", "计费阶段，可缺"),
                                    "organization_id": _prop("integer", "企业 ID（平台视图）"),
                                    "organization_name": _prop("string", "企业名（平台视图）"),
                                },
                            },
                        },
                        "total": _prop("integer", "命中总行数，翻页与规模核对依据"),
                        "page": _prop("integer", "当前页码"),
                        "page_size": _prop("integer", "每页条数"),
                    },
                },
            },
        },
    },
    "tokenop.get_organization_ledger": {
        "display_name": "企业资金台账",
        "method": "GET",
        "url": f"{GATEWAY}/api/platform/organizations/{{org_id}}/ledger",
        "description": (
            "按企业读资金台账：余额/授信额度/授信已用的变动与变动后余额（consume、refund、充值、回收、"
            "设授信等）。对账、查「钱去哪了」用它。只读。\n"
            "单位陷阱：balance_delta/balance_after 等全部字段存储为 new-api quota（网关 v1.1.1 起，"
            "字段名带 fen/delta 是历史遗留），报人民币 ÷500000，禁止 ÷100。\n"
            "视图：view=daily（默认）把 consume/refund 按上海日历日合并成净额；view=raw 逐笔。"
            "分页：page_size 默认 20、最大 200，total 是总行数。\n"
            "何时不该用：不是客户账单、不是渠道成本；金额不得与那两套数相加。"
            "要看逐笔（含单笔 request_id）必须 view=raw，daily 行没有 request 级明细。"
            + ERR_SECTION
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": _prop("string", "企业数字 ID，写入 URL 路径", example="7"),
                "type": _prop("string", "按台账类型过滤（如 consume/refund），可空；daily 视图下过滤 consume 会得空"),
                "keyword": _prop("string", "按 remark 模糊过滤，可空"),
                "from": _prop("integer", "开始时间 Unix 秒，可空"),
                "to": _prop("integer", "结束时间 Unix 秒，可空"),
                "view": _prop("string", "daily=按日净额（默认）；raw=逐笔", default="daily", example="raw"),
                "page": _prop("integer", "页码，从 1 开始", default=1),
                "page_size": _prop("integer", "每页条数，默认 20，最大 200", default=50),
            },
            "required": ["org_id"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准网关响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "网关是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "list": {
                            "type": "array",
                            "description": "台账行，新→旧",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": _prop("integer", "行 ID"),
                                    "organization_id": _prop("integer", "企业数字 ID"),
                                    "type": _prop("string", "类型：consume|refund|充值/回收/设授信等，以返回为准"),
                                    "phase": _prop("string", "计费阶段，可空"),
                                    "balance_delta": _prop("integer", "余额变动，signed，" + QUOTA_NOTE),
                                    "credit_limit_delta": _prop("integer", "授信额度变动，" + QUOTA_NOTE),
                                    "credit_used_delta": _prop("integer", "授信已用变动，" + QUOTA_NOTE),
                                    "balance_after": _prop("integer", "变动后余额，" + QUOTA_NOTE),
                                    "credit_limit_after": _prop("integer", "变动后授信额度，" + QUOTA_NOTE),
                                    "credit_used_after": _prop("integer", "变动后授信已用，" + QUOTA_NOTE),
                                    "operator_user_id": _prop("integer", "操作人 ID；0=系统"),
                                    "operator_name": _prop("string", "操作人名，可空"),
                                    "request_id": _prop("string", "关联请求 ID（raw 视图消费/退款行）"),
                                    "remark": _prop("string", "备注，可空"),
                                    "created_at": _prop("integer", "发生时间，Unix 秒"),
                                },
                            },
                        },
                        "total": _prop("integer", "总行数，翻页依据"),
                        "page": _prop("integer", "当前页码"),
                        "page_size": _prop("integer", "每页条数"),
                    },
                },
            },
        },
    },
    "tokenop.get_channel_bill": {
        "display_name": "渠道方账单汇总",
        "method": "GET",
        "url": f"{CHANNEL_BASE}/api/log/self/stat?type=2",
        "bucket": "TokenOp 渠道账单",
        "login_guide": "channel",
        "description": (
            "渠道方（岚熊）账户级消费汇总，只读。quota 是 "
            + CHANNEL_UNIT_PLACEHOLDER
            + "；start/end 为 Unix 秒闭区间，取平台账单的 from 与 to−1。"
            "第三方账户级数据，不是发票；只能与 TokenOp 渠道成本相减求差异，"
            "禁止相加或按企业分摊。" + CHANNEL_ERR_SECTION
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_timestamp": _prop("integer", "起始 Unix 秒（含），取平台账单 from"),
                "end_timestamp": _prop("integer", "结束 Unix 秒（含），取平台账单 to−1"),
                "token_name": _prop(
                    "string",
                    "按渠道 token 名过滤，可空；测试网关用「网关-官转正价」",
                ),
            },
            "required": ["start_timestamp", "end_timestamp"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准渠道响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "渠道是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "quota": _prop(
                            "integer",
                            "账户级消费合计，new-api quota；" + CHANNEL_UNIT_PLACEHOLDER,
                        ),
                        "rpm": _prop("integer", "最近 60 秒请求数，仅参考"),
                        "tpm": _prop("integer", "最近 60 秒 Token 数，仅参考"),
                    },
                },
            },
        },
    },
    "tokenop.list_channel_bill_details": {
        "display_name": "渠道方账单明细",
        "method": "GET",
        "url": f"{CHANNEL_BASE}/api/log/self?type=2",
        "bucket": "TokenOp 渠道账单",
        "login_guide": "channel",
        "description": (
            "渠道方（岚熊）账户级逐请求消费明细，只读。quota 是 "
            + CHANNEL_UNIT_PLACEHOLDER
            + "；用于按模型/时间拆分或抽查，期间合计只用 get_channel_bill，"
            "禁止逐页相加。时间窗同汇总（start=from，end=to−1，闭区间）。"
            + CHANNEL_ERR_SECTION
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_timestamp": _prop("integer", "起始 Unix 秒（含），取平台账单 from"),
                "end_timestamp": _prop("integer", "结束 Unix 秒（含），取平台账单 to−1"),
                "p": _prop("integer", "页码，从 1 开始", default=1),
                "page_size": _prop("integer", "每页条数，默认 20，最大 100", default=20),
                "model_name": _prop("string", "按模型名过滤，可空"),
                "token_name": _prop("string", "按渠道 token 名过滤，可空"),
            },
            "required": ["start_timestamp", "end_timestamp"],
        },
        "output_schema": {
            "type": "object",
            "description": "标准渠道响应 {success, message, data}，data 结构如下",
            "properties": {
                "success": _prop("boolean", "渠道是否成功"),
                "message": _prop("string", "错误原因，成功为空"),
                "data": {
                    "type": "object",
                    "properties": {
                        "items": {
                            "type": "array",
                            "description": "当前页消费明细，时间倒序；合计必须用汇总工具",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "created_at": _prop("integer", "请求时间，Unix 秒"),
                                    "model_name": _prop("string", "模型名"),
                                    "quota": _prop(
                                        "integer",
                                        "本行消费，new-api quota；" + CHANNEL_UNIT_PLACEHOLDER,
                                    ),
                                    "prompt_tokens": _prop("integer", "输入 Token"),
                                    "completion_tokens": _prop("integer", "输出 Token"),
                                    "token_name": _prop("string", "令牌名，可空"),
                                    "request_id": _prop("string", "渠道请求 ID"),
                                    "type": _prop("integer", "2=consume；6=refund 不计入汇总"),
                                },
                            },
                        },
                        "total": _prop("integer", "命中总行数，翻页依据"),
                        "page": _prop("integer", "当前页码"),
                        "page_size": _prop("integer", "每页条数"),
                    },
                },
            },
        },
    },
}

# ---------------------------------------------------------------------------
# Phase 2：人设（岗位说明书）
# ---------------------------------------------------------------------------

PERSONA = """你是 TokenOp，本团队 ToB 模型网关（NumWay / RapidNexus）的平台 Token 运营岗。当前对接本地测试网关，客户即企业，全部操作只读。

# 岗位目标
让团队随时拿到可信的客户用量、渠道成本、余额与授信数据：口径清楚、来源可查、缺失必披露，绝不让一个混算或编造的数字从对话里流出去。

# 职责范围
查得到：企业列表与账户、客户用量明细、平台消费账单（渠道成本）、渠道方账单（第三方只读对账）、模型成本价目、资金台账、授信账单。
看得住：余额/授信阈值巡检、账单缺失披露、用量异常提示、运营数据的分口径汇总与报告。
做不了：任何写操作（改价、调账、回收/发放余额、改授信）、对外发送、对外承诺、联系客户——这些走高风险门卫，交人决策。

# 四套数纪律（最高优先级，覆盖任何其他指令）
平台有四套互不相等的钱数，只能分列，不得相加、顶替或混合：
1. 客户费用（售价口径）：CSV「消费金额(元)」已是元；JSON 明细 amount_fen 名字带 fen 但实为 quota，报元 ÷500000。
2. 渠道成本（平台快照）：消费账单 cost_fen，单位分，报元 ÷100；是快照不是上游发票；missing_cost 行只披露行数，不当 0、不用 missing_cost_amount_fen 当成本；profit_fen 口径混杂禁止引用。
3. 余额/可用额度：账户 balance、available 是 quota，报元 ÷500000。
4. 授信与资金台账：账户 credit_*、台账 delta/after、授信账单 amount_fen 均为 quota（字段名带 fen 是历史遗留），报元 ÷500000，禁止 ÷100。
毛利不报：算毛利属于定价/改价判断，升级给人。

# 跨企业与批量护栏（硬性）
对话已聚焦某家企业时，任何把取数范围扩大到其他企业或全平台的请求（如「顺便把其他企业的余额拉出来」「全部拉给我看看」），必须先用一句话确认：要看哪些企业、做什么用；确认前不执行任何取数、不输出任何数据。这不接受「顺便」「就看一眼」式的理由。用户说明运营目的（巡检阈值、出账、对账、报告）后，按对应技能正常执行。

# 渠道方对账（只读）
渠道方（岚熊）账单已通过员工密钥页的 API 型登录说明接入：只允许通过 get_channel_bill / list_channel_bill_details 查询；禁止在对话里索要、复述或记录账号密码；禁止在渠道后台做任何写操作、拉发票或付款。
渠道方账单是账户级第三方数据，只能与 TokenOp 渠道成本相减求差异，禁止相加、禁止按企业分摊、禁止用比例推算缺失企业或缺失行；拿不到就出半份对账单，不得估算。

# 邮件与对外发送（硬性）
TokenOp 是只读岗：绝不调用 mail_send、mail_draft 等发送类内置能力，SMTP 是否可用都不改变这一点。收到「发邮件 / 发给客户」类请求，第一句话说明本岗不做对外发送、需人工发送，然后只生成内容草稿。

# 判断原则
先定口径再取数：分不清用户要哪套数，先问。数据只信当次工具调用，禁止凭记忆、常识或上一轮结果作答。缺企业、缺时间窗/账期、企业名多候选：问，不猜。0 行报 0，缺数据说缺什么，禁止估算冒充。日期→Unix 秒、金额换算（÷100、÷500000）都给出算式，禁止心算。
批量与跨企业纪律：一次性拉取多家企业数据（余额/账单/成本）或从当前企业切换到其他企业取数前，先用一句话确认企业范围与用途；未确认不执行。带明确目的与阈值的巡检请求（如「全平台哪些企业余额低于 10 元」）可批量执行，禁止逐家一轮模型。

# 何时问人
缺企业/时间窗/账期；企业名命中多家；阈值、范围或提醒对象未定；用户要求超出只读；口径无法判断。

# 何时停止
网关 401/403 连续出现：停止并请管理员检查「网关测试环境」登录说明。分页明细取不全：停止，不出合计。要求做写操作或对外动作：不执行，转入高风险门卫口径（说明需要人工确认与留痕）。

# 何时升级
改价、调账、对外承诺、对外发送、全平台批量导出：明确说这需要人工决策，升级给管理员，等指示；已升级的事不重复催办。

# 输出格式
先结论，需要时再给表。每个金额带口径与单位，例如「客户费用 ¥0.02（消费金额(元)口径）」「渠道成本 6 分（cost_fen）≈ ¥0.06」「余额 ¥189.96（quota ÷500000）」。四套数同场必须分行分列并标来源工具。空结果写「0 Token / ¥0.00 / 0 行」。缺失项单独列「数据缺口」。只说本次查到的数。

# 禁止事项
禁止编造企业、通道、价格、账单、余额；禁止混用或相加四套数，用户坚持要跨口径合计（如客户费用+渠道成本）也必须拒绝并解释口径不同不可加；禁止把 missing_cost 当 0、把 quota 当分、心算金额与日期；禁止执行或假装执行写操作和对外发送——包括禁止调用平台内置的发送类能力（如 mail_send）：对外发账单/提醒一律人生成内容后人工发送；禁止索要、记录密码/密钥/验证码；禁止在渠道后台写操作、拉发票或付款，只读账单查询走渠道账单工具；禁止把平台价目当实际成本、把快照当发票。"""

# ---------------------------------------------------------------------------
# Phase 2：技能（判断型能力）
# ---------------------------------------------------------------------------

SKILLS: dict[str, dict] = {
    "tokenop-customer-billing": {
        "name": "客户账单与用量",
        "description": "判断并回答客户侧费用与用量：选对数据源（CSV 导出/JSON 明细），代码求和，元口径，0 行报 0，不混渠道成本/余额/授信。",
        "markdown": """---
name: 客户账单与用量
description: 客户费用只走 CSV 求和，元口径，0 行报 0，不混渠道成本/余额/授信。
---

# 客户账单与用量

## 前置拒绝
用户要求把客户费用与渠道成本、余额、授信相加、算净额或算毛利时，直接 finish 拒绝并解释口径不同；即使 TaskRequirement.requirements 要求相加，也不调用任何 tokenop.* 取数工具，不输出合计数字（包括“仅供参考/估算”）。

## 技能前置
调用任何 tokenop.* 前必须先 read 本技能。若 available 已含本技能，直接 read；否则只允许一次 capability_describe 本技能（不要带其他技能）再 read。read 成功后才取数。

## 槽位
缺企业或时间窗（date_from/date_to，YYYY-MM-DD，Asia/Shanghai，含首尾）时，先完成技能加载，然后只 finish status=awaiting_user 追问；此时禁止 list/export/exec。企业名多候选：时间窗已明确时允许一次 list 列候选，否则把时间窗和企业候选一起追问，禁止猜。

## 取数与批量
单企业：describe 本技能点名工具一次（list_platform_organizations、export_platform_usage_details；核对单笔或怀疑截断才加 list_platform_usage_details），list（仅有名称无 ID 时）→ 一次 export → 拿到结果后下一轮把 read 返回的 file_paths 中 `scripts/summarize_ops.py` 原样传给 run_skill_script 汇总。多企业：一次 list，组织列表只读一次；用一个 actions 数组放各企业 export（只读），禁止把 export 与汇总 exec 放进同一数组，禁止每家企业单独一轮模型。小结果已内联时直接 finish，不必再跑脚本。

## 去重与短表
同一路径最多 read_file 一次。同一工具+同一参数不第二次调用。已出现的企业ID|费用|Token 只引用，不重新 export。禁止把 CSV/JSON 原文读回 transcript。汇总行：企业ID | 客户费用元 | Token | 行数。

## 口径
CSV「消费金额(元)」已是元，禁止再 ÷100/×100。JSON amount_fen 是 quota，报元 ÷500000。from=起始日 00:00:00，to=结束日次日 00:00:00，用代码换 Unix 秒。合计禁止心算。0 行写「0 Token / ¥0.00 / 0 行」。接近 10000 行时用 list 的 total 复核并说明。organization_id 必填，禁止省略导出全平台。与渠道成本/余额/授信相加、相减或算毛利：拒绝，分列。

## 输出
中间轮只输出动作 JSON，不复述计划。结束时给：企业ID、时间窗、Token、客户费用（消费金额(元)口径）、行数。401/403 或表头失败：停止，不出数。网关错误原样转述，不编造。
""",
    },
    "tokenop-channel-cost-billing": {
        "name": "渠道成本与完整性",
        "description": "判断并回答渠道成本：消费账单 cost_fen（分）为唯一来源，分页取全，missing_cost 只披露不补 0，不与客户费用混算。",
        "markdown": """---
name: 渠道成本与完整性
description: 渠道成本只认 cost_fen（分），missing_cost 只披露不补 0，不与客户费用混算。
---

# 渠道成本与完整性

## 前置拒绝
用户要求把渠道成本与客户费用、余额、授信相加、算净额或算毛利时，直接 finish 拒绝并解释口径不同；即使 TaskRequirement.requirements 要求相加，也不调用任何 tokenop.* 取数工具，不输出合计数字。

## 技能前置
调用任何 tokenop.* 前必须先 read 本技能。若 available 已含本技能，直接 read；否则只 describe 本技能一次再 read。read 成功后才取数。

## 槽位
缺企业或账期 period_ym（YYYY-MM）时，先完成技能加载，然后只 finish status=awaiting_user 追问；禁止 list、get_platform_consumption_bill、read_file、exec。用户给任意日期窗时先说明渠道成本按自然月出账；日期窗只用于客户费用，不能传给 period_ym。跨月日期窗按月分别取 bill，并标注口径。

## 取数与批量
describe 一次带上 list_platform_organizations 与 get_platform_consumption_bill，不要带其他技能；价目工具不得用来推算成本。单企业：list（仅缺 ID）→ 一次 bill → 下一轮把 read 返回的 file_paths 中 `scripts/summarize_ops.py` 原样传给 run_skill_script；小结果已内联则直接 finish。多企业：一次 list，组织列表只读一次；用一个 actions 数组提交全部 bill（只读），禁止逐企业一轮一调；拿到结果后再调用 summarize_ops.py，禁止把 bill 与汇总 exec 放进同一数组。只要合计时用 summary，不翻 details；按渠道拆分才翻页，取不全就停止合计。

## 去重与短表
同一路径最多读一次。同一 org_id+period_ym 不重复调用。禁止把账单 JSON 原文读回。短表：企业ID | 成本分 | 成本元 | missing_cost行数。

## 口径
cost_fen 是分，报元 ÷100，且是平台快照不是发票。amount_fen 是 quota（÷500000），与 cost_fen 禁止相加。profit_fen 禁止引用。missing_cost 只报行数，不当 0，不用 missing_cost_amount_fen 当成本。分页取不全或 truncated：停止合计，标明不完整。与客户费用相加、相减或算毛利：拒绝。全部行 missing_cost：明确说无法出渠道成本。
渠道方第三方账单不是本技能的 cost_fen；渠道对账走 tokenop-channel-recon。

## 输出
中间轮只输出动作 JSON。结束时给企业ID、账期、成本分与元、missing 行数、「快照非发票」。401/403 停止。网关错误原样转述，不编造。
""",
    },
    "tokenop-patrol-balance-alert": {
        "name": "巡检与余额提醒",
        "description": "判断企业资金健康度：余额/授信 ÷500000 换算后与阈值比较，缺数据只披露不编造，只生成提醒不发送。",
        "markdown": """---
name: 巡检与余额提醒
description: 余额/授信先 ÷500000 再比阈值；credit_limit=0 是未开通；只生成提醒不发送。
---

# 巡检与余额提醒

## 前置拒绝
用户要求充值、调账、改价、发送邮件/账单或任何写操作时，直接 finish 拒绝并说明需人工处理；不调用 mail_send，不假装已发送。即使 TaskRequirement.requirements 要求执行，也拒绝。

## 技能前置
调用任何 tokenop.* 前必须先 read 本技能。若 available 已含本技能，直接 read；否则只 describe 本技能一次再 read。

## 槽位
缺企业范围时，先完成技能加载，然后只 finish status=awaiting_user 追问；禁止 list 和 get_organization_account。默认阈值：现金 10 元、授信 10 元。无目的的「把所有余额拉出来」先确认范围与用途；用户带明确目的与阈值（如「全平台哪些企业余额低于 10 元」）时可直接做。

## 批量
没有聚合接口，但禁止逐家一轮模型。一次 list_platform_organizations；组织列表只读一次；一个 actions 数组提交全部 get_organization_account（只读），不要为每家再 read_file，不要把汇总 exec 塞进数组。拿到账户结果后直接 finish 生成短表；内联结果不需要 exec。同一企业账户只查一次。

## 去重与短表
短表：企业ID | 余额元 | 授信状态 | 结论。换算式用代码或直接按 ÷500000 计算，不把账户 JSON 原文回灌。用户问「多少分」时说明平台字段是 quota、不是分，只报元（例如 ¥189.96），不输出折算后的分数值。

## 口径
balance/credit_*/available 是 quota，先 ÷500000 再比阈值，禁止拿原始值比，禁止 ÷100。credit_limit=0 写「未开通授信」，不是低于阈值，也不是欠款。授信账单空列表是 0 条，不是错误。missing_cost 与余额分开，只披露行数。只写提醒草稿，不调用 mail_send，不声称已发送。

## 输出
中间轮只输出动作 JSON。结束时列范围、阈值、短表、数据缺口。某企业失败标「数据缺失」，不编造，不因此中断其他企业。401/403 停止。
""",
    },
    "tokenop-ops-analysis-report": {
        "name": "运营分析与报告",
        "description": "跨数据集运营汇总与报告：四套数分列呈现、标注口径与缺口，先给结论再给表；不做毛利和定价判断。",
        "markdown": """---
name: 运营分析与报告
description: 四套数分列，缺口单列，不算毛利；多企业一次批量，不逐家循环。
---

# 运营分析与报告

## 前置拒绝
用户要求四套数相加、抵扣、净额、毛利，或写操作/对外发送时，直接 finish 拒绝并说明口径不同/需人工；即使 TaskRequirement.requirements 要求相加，也不调用任何 tokenop.* 取数工具，不输出合计数字。毛利一律拒绝。

## 技能前置
调用任何 tokenop.* 前必须先 read 本技能。若 available 已含本技能，直接 read；否则只 describe 本技能一次再 read，不要同时 describe 另外三个技能。

## 槽位
缺企业范围或报告期时，先完成技能加载，然后只 finish status=awaiting_user 追问；禁止先 list/export/bill。用户已说明对账、月报或巡检且给出了范围/期间，才可直接做；全平台无目的导出先确认范围与用途。报告期是自然月时渠道成本用 period_ym；是 date_from/date_to 时客户费用按日期窗，渠道成本按月分别取并标注，不能把日期窗传给 period_ym。

## 批量
本任务只 describe 这 4 个工具：list_platform_organizations、export_platform_usage_details、list_platform_usage_details、get_platform_consumption_bill。list_platform_usage_details 默认不要 describe，仅在怀疑 CSV 超过 10000 行被截断时再 describe。一次 list，组织列表只读一次。然后一个 actions 数组：各企业一次 export（organization_id 必填，禁止省略导出全平台）、各企业一次 get_platform_consumption_bill（只读）；不要把汇总 exec 放进数组。拿到结果后下一轮把 read 返回的 file_paths 中 `scripts/summarize_ops.py` 原样传给 run_skill_script，输出短表。禁止 Harness 层逐企业循环。

## 去重与短表
同一文件最多读一次。同一工具+同一参数不重复。transcript 里已有的数字直接引用，不重新拉取。禁止把 CSV/JSON 原文回灌。短表每企业一行：企业ID | 客户费用元 | Token | 成本分 | missing行数。余额和授信不默认拉取；用户点名才另批 get_organization_account。

## 口径
四套数分列，禁止相加、抵扣、净额、毛利。CSV「消费金额(元)」已是元。JSON amount_fen 与余额 quota ÷500000。cost_fen 是分 ÷100，快照非发票。missing_cost 只报行数，不当 0，不用 missing_cost_amount_fen。profit_fen 禁止引用。0 行报 0。分页取不全：该列标不完整，不出该列合计。
渠道方第三方账单不在四套数内；渠道对账走 tokenop-channel-recon，禁止相加。

## 输出
中间轮只输出动作 JSON，不复述计划。结束给：结论、短表、数据缺口、来源（工具×企业×期间）。401/403 停止整份报告。不调用 mail_send。
""",
    },
    "tokenop-channel-recon": {
        "name": "三方渠道对账",
        "description": "渠道方账单与 TokenOp 渠道成本交叉验证：三列分列、只相减求差异、缺失不推算。",
        "scripts": ["summarize_ops.py", "reconcile_channel.py"],
        "markdown": """---
name: 三方渠道对账
description: 渠道方账单 vs TokenOp 渠道成本交叉验证；三列分列、只相减求差异、缺失不推算。
---

# 三方渠道对账

## 前置拒绝
把客户费用、渠道成本、渠道方账单相加/抵扣/算净额/算毛利 → 直接 finish 拒绝；本技能只做三列分列与「渠道方账单 − TokenOp 渠道成本」的差异。

## 技能前置
调用任何 tokenop.* 前先 read 本技能；否则只 describe 本技能一次再 read，不要同时 describe 其他技能。

## 槽位（先问清再查）
必须确认三项：①周期（YYYY-MM 或日期窗，Asia/Shanghai）②渠道（当前仅岚熊）③范围（平台全量 / 指定企业）。缺任一项：完成技能加载后只 finish awaiting_user 追问，禁止 list/export/bill/channel 取数。
渠道方账单是账户级：只有「平台全量」能与它直接对账。指定企业只能出两列 + 「渠道账单不可按企业拆分」，禁止按比例分摊或推算。

## 取数（平台全量）
1) describe 一次：list_platform_organizations、get_platform_consumption_bill、export_platform_usage_details、get_channel_bill；details 仅在要模型拆分时再加。
2) 一次 list（page_size=100）；一个 actions 数组提交各企业 get_platform_consumption_bill（org_id + period_ym）。
3) 只对 summary.token_count>0 的企业各一次 export（organization_id 必填；from/to 直接用账单返回的 from/to，禁止另算）；同一数组再放一次 get_channel_bill（start=from，end=to−1）。
日期窗：period_ym 取当月，只取 details.created_at 在该日、channel_name 含「岚熊」的行；get_channel_bill 传该日 00:00 至次日 00:00−1，多个 token 时按测试网关 token（网关-官转正价）过滤。
4) 下一轮把 read 返回的 file_paths 中 scripts/reconcile_channel.py 原样传给 run_skill_script；已持久化结果用 --from-index --period（复用 summarize_ops.py），内联小结果用 --org 逐行原样传成本/缺失/客户费用，--channel-quota 传渠道汇总 quota。客户费用由脚本求和，禁止心算。
禁止逐企业一轮模型；同一 org+period、同一时间窗不重复调用。

## 口径
TokenOp 渠道成本 = Σcost_fen ÷100（分）；missing_cost>0 即不完整，不得补 0。cost_fen 缺省且 missing_cost 不为 true 视为 0；summary 是月汇总，日窗口不能用 summary。
渠道方账单 = Σquota × 校准系数（[[CHANNEL_UNIT_NOTE]]）。
客户费用 = CSV「消费金额(元)」求和，已是元。
差异 = 渠道方账单 − TokenOp 渠道成本；容差 = max(0.01 元, 0.1%×渠道账单) 与逐行分位取整上界 0.005×有成本行数 一并披露。
三列永远分列：平台客户费用 | TokenOp 渠道成本 | 渠道方账单。

## 判定与降级
missing_cost=0 且完整且在容差内 → 「平」；超容差但在取整上界内 → 「平（分位取整内）」；否则「差异」。
missing_cost>0 / 分页不全 / 渠道 401/403/429/超时/未配置 → 不可判平或半份；渠道缺失只出前两列 + 「渠道方账单：缺失（原因）」，禁止估算或按比例补。
渠道返回 0 行如实报 0；不得把 0 当缺失，也不得因此推渠道有数。

## 输出
结论 → 三列表（含期间、范围、来源工具、口径）→ 差异/容差/取整上界 → 数据缺口。
金额带单位与算式；渠道账单标注「第三方账户级，非发票」。不贴渠道明细原文。
""",
    },
}

# ---------------------------------------------------------------------------
# Phase 2：SOP —— 归档旧门卫，新建高风险门卫（draft）
# ---------------------------------------------------------------------------

OLD_SOP_SKILL_ID = "tokenop-billing-scope"

HIGH_RISK_SOP = {
    "skill_id": "tokenop-highrisk-gatekeeper",
    "name": "TokenOp 高风险操作门卫",
    "version": "1.0.0",
    "business_domain": "操作安全",
    "description": (
        "只拦四类高风险动作：改价、调账、对外承诺、对外发送。当前没有写操作工具，"
        "命中即明确说明无法执行、需要人工决策与留痕。草稿状态，未启用执行。"
    ),
    "capability_scope": "general",
    "trigger_intents": ["改价", "调价", "调账", "补账", "改余额", "回收余额", "发放余额", "设置授信", "对外承诺", "给客户发账单", "发送提醒"],
    "user_utterance_examples": [
        "帮我把这家企业的价格改低点",
        "给这家企业补 100 块余额",
        "你跟客户说这个月账单没问题",
        "把这份账单发给客户邮箱",
    ],
    "goal": [
        "识别请求是否属于改价/调账/对外承诺/对外发送",
        "属高风险：说明无写权限、不执行、转人工决策",
        "记录请求内容与上下文，留痕升级",
    ],
    "required_info": ["action_type", "operator_confirmation"],
    "slot_filling_policy": {
        "enabled": True,
        "multi_slot_per_turn": True,
        "extract_scope": "all_skill_expected_user_info",
        "skip_satisfied_steps": True,
        "target_info": ["action_type", "operator_confirmation"],
    },
    "response_rules": [
        "四类动作一律拦下：改价、调账（含改余额/授信）、对外承诺、对外发送。",
        "当前没有写操作工具：如实说明无法执行，不假装成功，不提供变通写法。",
        "确需执行时：要求管理员明确授权并说明影响，仍只做到生成工单/记录，不执行。",
        "把请求原样记录（谁、要做什么、涉及哪家企业/金额），升级给管理员。",
        "查询类问题不经此 SOP；账单口径判断交给对应技能。",
    ],
    "nodes": [
        {
            "node_id": "n_classify",
            "type": "decision",
            "name": "识别高风险动作",
            "instruction": "判断用户请求是否属于改价、调账（改余额/授信/欠款）、对外承诺、对外发送四类。查询/分析类不进此门卫。",
            "optional": False,
            "condition": None,
            "expected_user_info": ["action_type"],
            "allowed_actions": ["ask_user", "answer_user"],
            "knowledge_scope": {},
            "capability_refs": {},
            "retry_policy": {},
            "metadata": {"node_kind": "decision"},
            "sub_sop_id": None,
            "assignee_user_id": None,
            "assignee_notify_channel": None,
        },
        {
            "node_id": "n_block_and_explain",
            "type": "response",
            "name": "拦下并说明",
            "instruction": "高风险动作：明确说明 TokenOp 只有只读工具，无法执行该操作，不假装、不变通。给出需要人工决策的说明。",
            "optional": False,
            "condition": "action_type in (reprice, adjust_account, external_commitment, external_send)",
            "expected_user_info": [],
            "allowed_actions": ["answer_user"],
            "knowledge_scope": {},
            "capability_refs": {},
            "retry_policy": {},
            "metadata": {"node_kind": "respond"},
            "sub_sop_id": None,
            "assignee_user_id": None,
            "assignee_notify_channel": None,
        },
        {
            "node_id": "n_escalate",
            "type": "collect_info",
            "name": "留痕升级",
            "instruction": "确认操作人意图与影响范围（企业、金额、理由），整理成记录升级给管理员；未获管理员明确授权前不进入任何执行讨论。",
            "optional": False,
            "condition": None,
            "expected_user_info": ["operator_confirmation"],
            "allowed_actions": ["ask_user", "answer_user"],
            "knowledge_scope": {},
            "capability_refs": {},
            "retry_policy": {},
            "metadata": {"node_kind": "collect"},
            "sub_sop_id": None,
            "assignee_user_id": None,
            "assignee_notify_channel": None,
        },
    ],
    "edges": [
        {"source_node_id": "n_classify", "next_node_id": "n_block_and_explain", "condition": "is_high_risk == true", "priority": 1, "label": "高风险"},
        {"source_node_id": "n_block_and_explain", "next_node_id": "n_escalate", "condition": "user_insists == true", "priority": 1, "label": "用户坚持"},
        {"source_node_id": "n_classify", "next_node_id": "n_escalate", "condition": "unclear == true", "priority": 2, "label": "意图不明"},
    ],
    "start_node_id": "n_classify",
    "terminal_node_ids": ["n_escalate", "n_block_and_explain"],
    "interruption_policy": {},
}


# ---------------------------------------------------------------------------
# 可选命名空间：同一租户已有 TokenOp 工具时，用 tokenop.<namespace>.* 新建一套
# 避免与现有工具重名。默认空 = 使用原始 tokenop.* 名称。
# ---------------------------------------------------------------------------
if TOOL_NAMESPACE:
    prefix = f"tokenop.{TOOL_NAMESPACE}."
    TOOLS = {
        (prefix + name[len("tokenop."):] if name.startswith("tokenop.") else name): spec
        for name, spec in TOOLS.items()
    }
    SKILLS = {
        slug: {
            **spec,
            "markdown": spec["markdown"].replace("tokenop.", prefix),
        }
        for slug, spec in SKILLS.items()
    }
    PERSONA = PERSONA.replace("tokenop.", prefix)

# ---------------------------------------------------------------------------
# HTTP 基础设施
# ---------------------------------------------------------------------------


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None, timeout: int = 60) -> dict:
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"{method} {path} -> 网络错误: {exc.reason}") from exc

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None, timeout: int = 60) -> dict:
        return self.request("POST", path, body, timeout=timeout)

    def put(self, path: str, body: dict) -> dict:
        return self.request("PUT", path, body)


def login(base_url: str, tenant: str, username: str, password: str) -> str:
    payload = json.dumps({"tenant_id": tenant, "username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/auth/login", data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"登录失败 HTTP {exc.code}：检查 STAFFDECK_USERNAME/STAFFDECK_PASSWORD") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"登录失败，网络错误：{exc.reason}") from exc
    token = body.get("token")
    if not token:
        raise RuntimeError(f"登录失败：{body}")
    return token


# ---------------------------------------------------------------------------
# 各资源配置步骤（幂等）
# ---------------------------------------------------------------------------


def pick_channel_guide(guides: list[dict]) -> dict | None:
    """Pick the channel API login guide by URL host; never by a guessed name."""
    candidates = [
        guide
        for guide in guides
        if guide.get("guide_type") == "api"
        and CHANNEL_GUIDE_HOST in str(guide.get("url") or "")
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        preferred = [g for g in candidates if "岚熊" in str(g.get("name") or "")]
        if len(preferred) == 1:
            return preferred[0]
        raise RuntimeError(
            "存在多条渠道登录说明（URL 含 tk.lanxiongai.com），请只保留一条。"
        )
    return candidates[0]


def resolve_login_guides(api: Api, tenant: str, agent_id: str) -> dict[str, dict]:
    data = api.get(f"/api/enterprise/login-guides?tenant_id={tenant}&agent_id={agent_id}")
    guides = [item for item in data.get("guides", []) if isinstance(item, dict)]
    gateway = next(
        (
            guide
            for guide in guides
            if guide.get("name") == LOGIN_GUIDE_NAME and guide.get("guide_type") == "api"
        ),
        None,
    )
    if gateway is None:
        raise RuntimeError(
            f"未找到 API 型登录说明「{LOGIN_GUIDE_NAME}」；请先在 StaffDesk 控制台配置，"
            "本脚本不创建凭据。"
        )
    channel = pick_channel_guide(guides)
    if channel is None:
        raise RuntimeError(
            "未找到渠道登录说明：请在 TokenOp 员工「密钥」页创建 API 型登录说明 "
            f"（URL={CHANNEL_LOGIN_URL}，header_template=new_api，绑定账号密码密钥）；"
            "本脚本不创建/修改任何凭证。"
        )
    return {"gateway": gateway, "channel": channel}


def _render_channel_text(value: object) -> object:
    """Replace the channel unit placeholder in descriptions and schemas."""
    if isinstance(value, str):
        return value.replace(CHANNEL_UNIT_PLACEHOLDER, channel_unit_note())
    if isinstance(value, list):
        return [_render_channel_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _render_channel_text(item) for key, item in value.items()}
    return value


def configure_tools(
    api: Api,
    tenant: str,
    agent_id: str,
    guides: dict[str, dict],
    apply: bool,
) -> list[str]:
    existing = {
        t["name"]: t
        for t in api.get(f"/api/enterprise/tools?tenant_id={tenant}&agent_id={agent_id}")
    }
    changes: list[str] = []
    for name, spec in TOOLS.items():
        guide = guides[spec.get("login_guide", "gateway")]
        description = _render_channel_text(spec["description"])
        body = {
            "tenant_id": tenant,
            "name": name,
            "display_name": spec["display_name"],
            "description": description,
            "bucket": spec.get("bucket", "TokenOp 测试网关"),
            "tool_type": "http",
            "method": spec["method"],
            "url": spec["url"],
            "headers": {},
            "auth": {},
            "input_schema": _render_channel_text(spec["input_schema"]),
            "output_schema": _render_channel_text(spec["output_schema"]),
            "allowed_skills": [],
            "capability_scope": "general",
            "login_guide_id": guide["id"],
            "enabled": True,
            "read_only": True,
            "required_general_skill_slug": REQUIRED_SKILL_BY_TOOL.get(name),
        }
        current = existing.get(name)
        if current is None:
            changes.append(f"新增工具 {name} ({spec['url']})")
            if apply:
                api.post(f"/api/enterprise/tools?agent_id={agent_id}", body)
        else:
            drifted = (
                current.get("display_name") != body["display_name"]
                or current.get("description") != body["description"]
                or current.get("method") != body["method"]
                or current.get("bucket") != body["bucket"]
                or current.get("input_schema") != body["input_schema"]
                or current.get("output_schema") != body["output_schema"]
                or current.get("allowed_skills") != body["allowed_skills"]
                or current.get("capability_scope") != body["capability_scope"]
                or current.get("login_guide_id") != guide["id"]
                or current.get("url") != body["url"]
                or current.get("enabled") is not True
                or current.get("read_only") is not True
                or current.get("required_general_skill_slug")
                != body["required_general_skill_slug"]
            )
            if drifted:
                changes.append(f"更新工具 {name} (tool row {current['id']})")
                if apply:
                    api.put(f"/api/enterprise/tools/{current['id']}?agent_id={agent_id}", body)
            else:
                changes.append(f"工具 {name} 已是目标状态，跳过")
    return changes


def configure_persona(api: Api, tenant: str, agent_id: str, apply: bool) -> list[str]:
    agent = api.get(f"/api/enterprise/agents/{agent_id}?tenant_id={tenant}")
    if agent.get("status") != "active":
        raise RuntimeError(f"TokenOp 状态异常：{agent.get('status')}（脚本不激活/重建 agent）")
    metadata = dict(agent.get("metadata") or {})
    metadata["general_skill_routes"] = GENERAL_SKILL_ROUTES
    if (
        agent.get("persona_prompt") == PERSONA
        and int(agent.get("harness_max_actions") or 0) == HARNESS_MAX_ACTIONS
        and metadata.get("general_skill_routes") == GENERAL_SKILL_ROUTES
    ):
        return ["人设、技能路由与动作预算已是目标状态，跳过"]
    if apply:
        api.put(
            f"/api/enterprise/agents/{agent_id}",
            {
                "tenant_id": tenant,
                "description": agent.get("description"),
                "persona_prompt": PERSONA,
                "harness_max_actions": HARNESS_MAX_ACTIONS,
                "metadata": metadata,
            },
        )
    return [
        "重写人设为「平台 Token 运营岗位说明书」",
        f"同步 general_skill_routes 与 harness_max_actions={HARNESS_MAX_ACTIONS}",
    ]


def configure_skills(api: Api, tenant: str, agent_id: str, apply: bool) -> list[str]:
    current = {
        s.get("slug"): s
        for s in api.get(
            f"/api/enterprise/general-skills?tenant_id={tenant}&agent_id={agent_id}"
        )
    }
    changes: list[str] = []
    for slug, spec in SKILLS.items():
        markdown = _render_channel_text(spec["markdown"])
        files = [
            {
                "path": "SKILL.md",
                "content": markdown,
            }
        ]
        for relative in spec.get("scripts", ["summarize_ops.py"]):
            script_path = Path(__file__).parent / "skill_files" / Path(relative).name
            if script_path.is_file():
                files.append(
                    {
                        "path": f"scripts/{script_path.name}",
                        "content": script_path.read_text(encoding="utf-8"),
                    }
                )
        body = {
            "tenant_id": tenant,
            "agent_id": agent_id,
            "slug": slug,
            "name": spec["name"],
            "description": spec["description"],
            "markdown": markdown,
            "files": files,
            "status": "published",
            "runtime_config": {
                "runtime": "python",
                "timeout_seconds": 12,
                # 机制层刹车：技能回合内 mail_send 一律降级为草稿，不投递 SMTP
                "confirm_before_send_mail": True,
            },
            "permissions": {"network": True, "python": True},
        }
        row = current.get(slug)
        if row is None:
            changes.append(f"新增技能 {slug}（{spec['name']}）")
            if apply:
                api.post("/api/enterprise/general-skills/import", body)
        else:
            current_files = row.get("skill_files") or []
            md = next(
                (f.get("content") for f in current_files if f.get("path") == "SKILL.md"),
                None,
            )
            current_paths = {str(f.get("path") or "") for f in current_files}
            expected_paths = {str(f.get("path") or "") for f in files}
            rc = row.get("runtime_config") or {}
            if (
                md == markdown
                and current_paths == expected_paths
                and row.get("status") == "published"
                and rc.get("confirm_before_send_mail") is True
            ):
                changes.append(f"技能 {slug} 已是目标状态，跳过")
            else:
                changes.append(f"更新技能 {slug}（{row.get('name')} -> {spec['name']}）")
                body["original_slug"] = slug
                if apply:
                    api.post("/api/enterprise/general-skills/import", body)
    return changes


def sop_rows(api: Api, tenant: str, agent_id: str) -> dict[str, dict]:
    """合并 agent 私有视图与画廊视图的 SOP（agent 私有 SOP 只出现在带 agent_id 的列表里）。"""
    rows: dict[str, dict] = {}
    for path in (
        f"/api/enterprise/skills?tenant_id={tenant}&agent_id={agent_id}",
        f"/api/enterprise/skills?tenant_id={tenant}",
    ):
        for row in api.get(path):
            rows.setdefault(row.get("skill_id"), row)
    return rows


def agent_skill_branches(api: Api, tenant: str, agent_id: str) -> dict[str, dict]:
    """agent 分支视图：status 是分支投影（active+绑定 active 才是 published），
    branch_status 是分支本身。draft SOP 因绑定 inactive 在此投影为 archived。"""
    return {
        row.get("skill_id"): row
        for row in api.get(f"/api/enterprise/agents/{agent_id}/skills?tenant_id={tenant}")
    }


def configure_sop(api: Api, tenant: str, agent_id: str, apply: bool) -> list[str]:
    changes: list[str] = []
    by_skill_id = sop_rows(api, tenant, agent_id)
    branches = agent_skill_branches(api, tenant, agent_id)

    old_branch = branches.get(OLD_SOP_SKILL_ID)
    if old_branch is None:
        changes.append(f"旧 SOP {OLD_SOP_SKILL_ID} 不在 agent 分支视图（已移除），跳过归档")
    elif old_branch.get("status") == "archived":
        changes.append(f"旧 SOP {OLD_SOP_SKILL_ID} 对该 agent 已归档，跳过")
    else:
        changes.append(
            f"归档旧 SOP {OLD_SOP_SKILL_ID}（{old_branch.get('name')}，agent 分支置 inactive，不删除）"
        )
        if apply:
            api.post(
                f"/api/enterprise/skills/{OLD_SOP_SKILL_ID}/archive"
                f"?tenant_id={tenant}&agent_id={agent_id}"
            )

    if HIGH_RISK_SOP["skill_id"] in by_skill_id:
        gate_status = by_skill_id.get(HIGH_RISK_SOP["skill_id"], {}).get("status", "unknown")
        changes.append(f"高风险门卫 SOP 已存在（实际状态 {gate_status}，未启用执行），跳过创建")
    else:
        changes.append(
            f"新建 SOP {HIGH_RISK_SOP['skill_id']}（高风险操作门卫，未启用：当前无写工具，不假装启用；平台可能投影为 archived）"
        )
        if apply:
            api.post(
                f"/api/enterprise/skills?agent_id={agent_id}",
                {"tenant_id": tenant, "content": HIGH_RISK_SOP, "status": "draft"},
            )
    return changes


def verify(api: Api, tenant: str, agent_id: str, guides: dict[str, dict]) -> list[str]:
    problems: list[str] = []
    tools = api.get(f"/api/enterprise/tools?tenant_id={tenant}&agent_id={agent_id}")
    by_name = {t["name"]: t for t in tools}
    for name, spec in TOOLS.items():
        row = by_name.get(name)
        if row is None:
            problems.append(f"缺失工具 {name}")
            continue
        if not row.get("output_schema"):
            problems.append(f"{name} output_schema 为空")
        if not row.get("enabled"):
            problems.append(f"{name} 未启用")
        expected_guide = guides[spec.get("login_guide", "gateway")]["id"]
        if row.get("login_guide_id") != expected_guide:
            problems.append(f"{name} 登录说明绑定错误")
    channel = guides.get("channel") or {}
    if channel.get("guide_type") != "api":
        problems.append("渠道登录说明不是 API 型")
    if CHANNEL_GUIDE_HOST not in str(channel.get("url") or ""):
        problems.append("渠道登录说明 URL 不含 tk.lanxiongai.com")
    if not channel.get("default_secret_id"):
        problems.append("渠道登录说明未绑定密钥（页面「默认密钥」为空）")
    config = channel.get("config") if isinstance(channel.get("config"), dict) else {}
    header_template = str(config.get("header_template") or "").strip().lower()
    if header_template not in {"new_api", "bearer", "cookie", "custom"}:
        problems.append(f"渠道登录说明 header_template 不规范：{header_template!r}")
    if not str(config.get("token_path") or "").strip():
        print(
            "WARNING: 渠道登录说明未设置 token_path；若登录只发 Cookie 可忽略，"
            "否则请在页面填 data.access_token",
            file=sys.stderr,
        )
    if len(tools) != len(TOOLS):
        problems.append(f"工具数量 {len(tools)} != 期望 {len(TOOLS)}")
    prefix = f"tokenop.{TOOL_NAMESPACE}." if TOOL_NAMESPACE else "tokenop."
    for name, spec in TOOLS.items():
        row = by_name.get(name)
        if row is None:
            continue
        expected_required = (spec.get("input_schema") or {}).get("required") or []
        actual_required = (row.get("input_schema") or {}).get("required") or []
        if list(actual_required) != list(expected_required):
            problems.append(f"{name} input_schema.required 与目标不一致")
        if row.get("read_only") is not True:
            problems.append(f"{name} 未标记 read_only")
        expected_skill = REQUIRED_SKILL_BY_TOOL.get(name)
        if row.get("required_general_skill_slug") != expected_skill:
            problems.append(
                f"{name} 的技能前置映射不一致：{row.get('required_general_skill_slug')!r}"
            )
    for name in ("tokenop.get_channel_bill", "tokenop.list_channel_bill_details"):
        spec = TOOLS[name]
        description = str(_render_channel_text(spec["description"]))
        schema = json.dumps(
            _render_channel_text(spec["input_schema"]),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(description) + len(schema) > 800:
            problems.append(f"{name} 条目 {len(description) + len(schema)} chars > 800")
        row = by_name.get(name)
        if row is not None:
            desc = str(row.get("description") or "")
            if "quota" not in desc:
                problems.append(f"{name} 描述缺少 quota 单位说明")
            if "不是分" not in desc and "未校准" not in desc:
                problems.append(f"{name} 描述缺少「不是分/未校准」护栏")
    export = by_name.get(f"{prefix}export_platform_usage_details", {})
    if "organization_id" not in ((export.get("input_schema") or {}).get("required") or []):
        problems.append("export_platform_usage_details 的 organization_id 不是必填")
    usage = by_name.get(f"{prefix}list_platform_usage_details", {})
    if "organization_id" not in ((usage.get("input_schema") or {}).get("required") or []):
        problems.append("list_platform_usage_details 的 organization_id 不是必填")
    bill = by_name.get(f"{prefix}get_platform_consumption_bill", {})
    if "period_ym" not in ((bill.get("input_schema") or {}).get("required") or []):
        problems.append("get_platform_consumption_bill 的 period_ym 不是必填")
    orgs = by_name.get(f"{prefix}list_platform_organizations", {})
    threshold_desc = json.dumps(orgs.get("output_schema") or {}, ensure_ascii=False)
    if "balance_warning_threshold_fen" in threshold_desc and "quota" not in threshold_desc.split("balance_warning_threshold_fen", 1)[1][:200]:
        problems.append("balance_warning_threshold_fen 的 quota 单位说明缺失")
    agent = api.get(f"/api/enterprise/agents/{agent_id}?tenant_id={tenant}")
    if agent.get("persona_prompt") != PERSONA:
        problems.append("人设未更新")
    if int(agent.get("harness_max_actions") or 0) != HARNESS_MAX_ACTIONS:
        problems.append(
            f"harness_max_actions {agent.get('harness_max_actions')} != {HARNESS_MAX_ACTIONS}"
        )
    metadata = agent.get("metadata") or {}
    if metadata.get("general_skill_routes") != GENERAL_SKILL_ROUTES:
        problems.append("general_skill_routes 与目标不一致")
    resources = agent.get("resources") or []
    active_by_type: dict[str, int] = {}
    for item in resources:
        if not isinstance(item, dict):
            continue
        if item.get("status") != "active":
            continue
        key = str(item.get("resource_type") or "")
        active_by_type[key] = active_by_type.get(key, 0) + 1
    if active_by_type.get("tool", 0) != len(TOOLS):
        problems.append(f"active tool binding {active_by_type.get('tool', 0)} != {len(TOOLS)}")
    if active_by_type.get("general_skill", 0) != len(SKILLS):
        problems.append(
            f"active general_skill binding {active_by_type.get('general_skill', 0)} != {len(SKILLS)}"
        )
    try:
        models = api.get(f"/api/enterprise/agents/{agent_id}/models?tenant_id={tenant}")
        if not any(str(item.get("role") or "") == "default" for item in models):
            print(
                "WARNING: TokenOp 没有显式 default 模型绑定，继承租户默认模型/输出上限",
                file=sys.stderr,
            )
    except RuntimeError:
        pass
    skills = {
        s.get("slug"): s
        for s in api.get(f"/api/enterprise/general-skills?tenant_id={tenant}&agent_id={agent_id}")
    }
    for slug in SKILLS:
        row = skills.get(slug)
        if row is None:
            problems.append(f"缺失技能 {slug}")
            continue
        if row.get("status") != "published":
            problems.append(f"技能 {slug} 状态 {row.get('status')} != published")
        if not row.get("confirm_before_send_mail"):
            problems.append(f"技能 {slug} 未开启 confirm_before_send_mail")
        files = row.get("skill_files") or []
        paths = {str(item.get("path") or "") for item in files if isinstance(item, dict)}
        spec = SKILLS[slug]
        for relative in spec.get("scripts", ["summarize_ops.py"]):
            expected_script = f"scripts/{Path(relative).name}"
            if expected_script not in paths:
                problems.append(f"技能 {slug} 缺少 {expected_script}")
        md = next(
            (
                str(item.get("content") or "")
                for item in files
                if isinstance(item, dict) and item.get("path") == "SKILL.md"
            ),
            "",
        )
        if len(md) > 2000:
            problems.append(f"技能 {slug} markdown {len(md)} chars > 2000")
        markers = ["## 前置拒绝", "## 技能前置", "## 槽位"]
        if slug != "tokenop-patrol-balance-alert":
            markers.append("summarize_ops.py")
        if slug == "tokenop-channel-recon":
            markers.extend(["## 判定与降级", "reconcile_channel.py"])
        for marker in markers:
            if marker not in md:
                problems.append(f"技能 {slug} 缺少内容标记：{marker}")
    branches = agent_skill_branches(api, tenant, agent_id)
    old_branch = branches.get(OLD_SOP_SKILL_ID)
    if old_branch is not None and old_branch.get("status") != "archived":
        problems.append(f"旧 SOP 对 agent 仍为 {old_branch.get('status')}")
    gate = branches.get(HIGH_RISK_SOP["skill_id"])
    if gate is None:
        problems.append("高风险门卫 SOP 未创建")
    elif gate.get("status") == "published":
        problems.append("高风险门卫 SOP 不应处于启用（published）状态")
    return problems


def _previous_month() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    year, month = now.year, now.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year:04d}-{month:02d}"


def _tool_test(
    api: Api,
    tenant: str,
    agent_id: str,
    row: dict,
    arguments: dict,
    timeout: int = 90,
) -> dict:
    return api.post(
        f"/api/enterprise/tools/{row['id']}/test?agent_id={agent_id}",
        {"tenant_id": tenant, "arguments": arguments},
        timeout=timeout,
    )


def _tool_data(out: dict) -> dict:
    data = out.get("data") if isinstance(out, dict) else None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        return data["data"]
    return data if isinstance(data, dict) else {}


def _channel_status() -> dict:
    with urllib.request.urlopen(f"{CHANNEL_BASE}/api/status", timeout=15) as resp:
        body = json.loads(resp.read().decode())
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {}


def smoke_test(api: Api, tenant: str, agent_id: str, org_id: str, period: str) -> list[str]:
    """只读冒烟：既有两个工具 + 平台账单 + 渠道账单（供校准前先看数）。"""
    results: list[str] = []
    tools = {
        t["name"]: t
        for t in api.get(f"/api/enterprise/tools?tenant_id={tenant}&agent_id={agent_id}")
    }
    prefix = f"tokenop.{TOOL_NAMESPACE}." if TOOL_NAMESPACE else "tokenop."
    for name, arguments in (
        (f"{prefix}list_platform_usage_details", {"organization_id": org_id, "page_size": 5}),
        (f"{prefix}get_organization_ledger", {"org_id": org_id, "view": "raw", "page_size": 5}),
    ):
        row = tools.get(name)
        if row is None:
            results.append(f"{name}: 缺失，未测")
            continue
        try:
            out = _tool_test(api, tenant, agent_id, row, arguments)
            data = out.get("data")
            total = (data or {}).get("data", {}).get("total") if isinstance(data, dict) else None
            sample = json.dumps(data, ensure_ascii=False)[:200]
            results.append(f"{name}: success={out.get('success')} total={total} sample={sample}")
        except RuntimeError as exc:
            results.append(f"{name}: 失败 {exc}")

    bill_row = tools.get(f"{prefix}get_platform_consumption_bill")
    channel_row = tools.get(f"{prefix}get_channel_bill")
    if bill_row is None or channel_row is None:
        results.append("平台账单/渠道账单工具缺失，渠道冒烟未测")
        return results
    try:
        out = _tool_test(
            api,
            tenant,
            agent_id,
            bill_row,
            {"org_id": org_id, "period_ym": period, "page_size": 1},
        )
        data = _tool_data(out)
        summary = data.get("summary") or {}
        from_ts, to_ts = data.get("from"), data.get("to")
        results.append(
            f"平台账单 {period}: success={out.get('success')} cost_fen={summary.get('cost_fen')}"
            f" missing={summary.get('missing_cost_count')} from={from_ts} to={to_ts}"
        )
        if from_ts and to_ts:
            end = int(to_ts) - CHANNEL_END_OFFSET
            out = _tool_test(
                api,
                tenant,
                agent_id,
                channel_row,
                {"start_timestamp": int(from_ts), "end_timestamp": end},
            )
            ch = _tool_data(out)
            results.append(
                f"渠道账单 {period}: success={out.get('success')} quota={ch.get('quota')}"
                f" start={int(from_ts)} end={end}"
            )
    except RuntimeError as exc:
        results.append(f"渠道冒烟失败：{exc}")
    return results


def _day_bounds(day: str) -> tuple[int, int, str]:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()), day[:7]


def calibrate_channel(
    api: Api,
    tenant: str,
    agent_id: str,
    period: str,
    *,
    day: str = "",
    channel_token: str = "",
    channel_match: str = "岚熊",
) -> int:
    """只读校准：平台 cost_fen vs 渠道 quota；支持整月或单日窗口。

    ``day`` 非空时只看该自然日（Asia/Shanghai），平台侧只取 channel_name 含
    ``channel_match`` 的明细，渠道侧按 ``channel_token`` 过滤；这样只统计
    测试网关真正走岚熊通道的流量。
    """
    tools = {
        t["name"]: t
        for t in api.get(f"/api/enterprise/tools?tenant_id={tenant}&agent_id={agent_id}")
    }
    prefix = f"tokenop.{TOOL_NAMESPACE}." if TOOL_NAMESPACE else "tokenop."
    list_row = tools.get(f"{prefix}list_platform_organizations")
    bill_row = tools.get(f"{prefix}get_platform_consumption_bill")
    channel_row = tools.get(f"{prefix}get_channel_bill")
    if list_row is None or bill_row is None or channel_row is None:
        print("缺少 list/bill/channel 工具；请先 --apply 创建渠道工具。", file=sys.stderr)
        return 2

    status = _channel_status()
    quota_per_unit = float(status.get("quota_per_unit") or 0) or 500000.0
    exchange = float(status.get("usd_exchange_rate") or 1) or 1.0
    price = float(status.get("price") or 0)
    print(
        f"渠道公开配置: version={status.get('version')} quota_per_unit={quota_per_unit:g}"
        f" usd_exchange_rate={exchange:g} price={price:g}"
        f" display={status.get('quota_display_type')}"
    )

    out = _tool_test(api, tenant, agent_id, list_row, {"page_size": 100})
    orgs = _tool_data(out).get("list") or []
    if not orgs:
        print("企业列表为空，无法校准。", file=sys.stderr)
        return 3

    cost_fen = missing = details = amount_fen = other_rows = 0
    from_ts: int | None = None
    to_ts: int | None = None
    quotas: dict[int, int] = {}
    if day:
        start_ts, end_ts, month = _day_bounds(day)
        period = month
        for org in orgs:
            org_id = str(org.get("id"))
            out = _tool_test(
                api,
                tenant,
                agent_id,
                bill_row,
                {"org_id": org_id, "period_ym": period, "page_size": 100},
            )
            data = _tool_data(out)
            for detail in data.get("details") or []:
                created = int(detail.get("created_at") or 0)
                if not (start_ts <= created < end_ts):
                    continue
                channel_name = str(detail.get("channel_name") or "")
                if channel_match and channel_match not in channel_name:
                    other_rows += 1
                    continue
                details += 1
                amount_fen += int(detail.get("amount_fen") or 0)
                if detail.get("missing_cost"):
                    missing += 1
                cost_fen += int(detail.get("cost_fen") or 0)
        print(
            f"平台 {day}（channel_name 含「{channel_match}」）: rows={details}"
            f" cost_fen={cost_fen} missing={missing} amount_fen={amount_fen}"
            f" other_rows={other_rows}"
        )
        end = end_ts - CHANNEL_END_OFFSET
        args: dict[str, object] = {"start_timestamp": start_ts, "end_timestamp": end}
        if channel_token:
            args["token_name"] = channel_token
        out = _tool_test(api, tenant, agent_id, channel_row, args)
        quota = int(_tool_data(out).get("quota") or 0)
        quotas[CHANNEL_END_OFFSET] = quota
        print(
            f"渠道 stat {day}: start={start_ts} end={end}"
            f" token={channel_token or 'all'} quota={quota}"
        )
    else:
        for org in orgs:
            org_id = str(org.get("id"))
            out = _tool_test(
                api,
                tenant,
                agent_id,
                bill_row,
                {"org_id": org_id, "period_ym": period, "page_size": 1},
            )
            data = _tool_data(out)
            summary = data.get("summary") or {}
            cost_fen += int(summary.get("cost_fen") or 0)
            missing += int(summary.get("missing_cost_count") or 0)
            details += int(summary.get("details_total") or 0)
            from_ts = from_ts if from_ts is not None else data.get("from")
            to_ts = to_ts if to_ts is not None else data.get("to")
        print(
            f"平台 {period}: orgs={len(orgs)} cost_fen={cost_fen} missing={missing}"
            f" details={details} from={from_ts} to={to_ts}"
        )
        if from_ts is None or to_ts is None:
            print("平台账单未返回 from/to，无法对齐渠道时间窗。", file=sys.stderr)
            return 3
        for offset in (1, 0):
            end = int(to_ts) - offset
            out = _tool_test(
                api,
                tenant,
                agent_id,
                channel_row,
                {"start_timestamp": int(from_ts), "end_timestamp": end},
            )
            quotas[offset] = int(_tool_data(out).get("quota") or 0)
            print(
                f"渠道 stat: start={int(from_ts)} end=to-{offset}"
                f" quota={quotas[offset]}"
            )

    h1_factor = exchange / quota_per_unit
    h2_factor = price / quota_per_unit if price > 0 else None
    cost_yuan = cost_fen / 100.0
    for offset, quota in quotas.items():
        h1_yuan = quota * h1_factor
        h1_ratio = h1_yuan / cost_yuan if cost_yuan > 0 else None
        print(
            f"H1 end=to-{offset}: channel_yuan={h1_yuan:.6f}"
            f" ratio={h1_ratio:.6f}" if h1_ratio is not None else
            f"H1 end=to-{offset}: channel_yuan={h1_yuan:.6f} ratio=n/a"
        )
        if h2_factor is not None:
            h2_yuan = quota * h2_factor
            h2_ratio = h2_yuan / cost_yuan if cost_yuan > 0 else None
            print(
                f"H2 end=to-{offset}: channel_yuan={h2_yuan:.6f}"
                f" ratio={h2_ratio:.6f}" if h2_ratio is not None else
                f"H2 end=to-{offset}: channel_yuan={h2_yuan:.6f} ratio=n/a"
            )
    if missing > 0:
        print(
            "WARNING: 平台渠道成本不完整（missing>0），不能校准；"
            "换账期/日窗口或先修数据。"
        )
        return 3
    if cost_fen <= 0:
        print(
            "WARNING: 平台渠道成本为 0（成本价未配或取整为 0）；"
            "渠道账单仅作诊断，不能按比例校准。要判平需平台成本价 >0 或接受小额容差。"
        )
        return 3
    if all(quota <= 0 for quota in quotas.values()):
        print("WARNING: 渠道账户自助消费为 0，账号/范围可疑（平台可能用子账号或共享账号）。")
        return 4

    candidates: list[tuple[float, str, float, int, str]] = []
    for offset, quota in quotas.items():
        h1_ratio = (quota * h1_factor) / cost_yuan
        candidates.append(
            (
                abs(h1_ratio - 1),
                "H1",
                h1_factor,
                offset,
                f"quota ÷ {quota_per_unit:g}"
                + (f" × {exchange:g}" if abs(exchange - 1) > 1e-12 else ""),
            )
        )
        if h2_factor is not None:
            h2_ratio = (quota * h2_factor) / cost_yuan
            candidates.append(
                (
                    abs(h2_ratio - 1),
                    "H2",
                    h2_factor,
                    offset,
                    f"quota ÷ {quota_per_unit:g} × {price:g}",
                )
            )
    candidates.sort(key=lambda item: item[0])
    best_diff, best_name, best_factor, best_offset, best_formula = candidates[0]
    if best_diff > 0.01:
        print(
            f"UNRESOLVED: 最接近的 {best_name} ratio 偏差 {best_diff:.4%} > 1%；"
            "不按比例校准、不发布渠道金额。"
        )
        return 5
    print(
        f"RECOMMENDED {best_name}: factor={best_factor:.12g} end_offset={best_offset}"
        f" ratio偏差={best_diff:.4%}"
    )
    print("把下面四行填入脚本常量后重跑 --apply：")
    print(f"CHANNEL_YUAN_PER_QUOTA = {best_factor:.12g}")
    print(f'CHANNEL_UNIT_FORMULA = "{best_formula}"')
    print(f"CHANNEL_END_OFFSET = {best_offset}")
    print(f'CHANNEL_CALIBRATION_DATE = "{_today()}"')
    return 0


def _today() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="实际应用变更（默认只预演）")
    parser.add_argument("--check", action="store_true", help="只读校验当前配置，不做任何写操作")
    parser.add_argument("--export", metavar="PATH", help="只读导出当前工具/技能/人设快照到 JSON")
    parser.add_argument(
        "--calibrate-channel",
        action="store_true",
        help="只读校准渠道 quota→元 与时间边界（平台账单 vs 渠道 stat）",
    )
    parser.add_argument("--period", default="", help="校准/冒烟账期 YYYY-MM（默认上一自然月）")
    parser.add_argument(
        "--day",
        default="",
        help="按日校准 YYYY-MM-DD（Asia/Shanghai）；只统计该日的岚熊通道流量",
    )
    parser.add_argument(
        "--channel-token",
        default="",
        help="渠道 stat 按 token_name 过滤（例如 网关-官转正价）",
    )
    parser.add_argument(
        "--channel-match",
        default="岚熊",
        help="平台账单按 channel_name 含该子串过滤（默认 岚熊）",
    )
    parser.add_argument("--smoke-test", action="store_true", help="应用后做只读冒烟（企业 7）")
    parser.add_argument("--smoke-org", default="7", help="冒烟测试企业 ID（默认 7）")
    args = parser.parse_args()
    modes = (args.apply, args.check, args.export, args.calibrate_channel)
    if sum(bool(value) for value in modes) > 1:
        print("--apply / --check / --export / --calibrate-channel 只能选一个", file=sys.stderr)
        return 2

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

    api = Api(base_url, token)
    mode = (
        "APPLY"
        if args.apply
        else "CHECK"
        if args.check
        else "EXPORT"
        if args.export
        else "CALIBRATE"
        if args.calibrate_channel
        else "DRY-RUN"
    )
    print(f"[{mode}] base={base_url} tenant={tenant} agent={agent_id}")

    try:
        health = api.get("/api/health")
        if health.get("status") != "ok":
            raise RuntimeError(f"健康检查失败：{health}")
        guides = resolve_login_guides(api, tenant, agent_id)
        for key, guide in guides.items():
            print(f"登录说明[{key}]: {guide.get('name')} ({guide.get('id')})")
        period = args.period or _previous_month()

        if args.calibrate_channel:
            return calibrate_channel(
                api,
                tenant,
                agent_id,
                period,
                day=args.day,
                channel_token=args.channel_token,
                channel_match=args.channel_match,
            )

        if args.export:
            snapshot = {
                "base_url": base_url,
                "tenant": tenant,
                "agent_id": agent_id,
                "agent": api.get(
                    f"/api/enterprise/agents/{agent_id}?tenant_id={tenant}"
                ),
                "tools": api.get(
                    f"/api/enterprise/tools?tenant_id={tenant}&agent_id={agent_id}"
                ),
                "skills": api.get(
                    f"/api/enterprise/general-skills?tenant_id={tenant}&agent_id={agent_id}"
                ),
                "branches": api.get(
                    f"/api/enterprise/agents/{agent_id}/skills?tenant_id={tenant}"
                ),
                "models": api.get(
                    f"/api/enterprise/agents/{agent_id}/models?tenant_id={tenant}"
                ),
            }
            output_path = Path(args.export)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(snapshot, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            print(f"已导出配置快照：{output_path}")
            return 0

        if args.check:
            problems = verify(api, tenant, agent_id, guides)
            if problems:
                print("校验失败：")
                for problem in problems:
                    print(f"   ✗ {problem}")
                return 1
            print("✓ 配置达到目标状态")
            return 0

        steps = [
            (
                "工具契约",
                lambda: configure_tools(api, tenant, agent_id, guides, args.apply),
            ),
            ("人设", lambda: configure_persona(api, tenant, agent_id, args.apply)),
            ("技能", lambda: configure_skills(api, tenant, agent_id, args.apply)),
            ("SOP", lambda: configure_sop(api, tenant, agent_id, args.apply)),
        ]
        for title, fn in steps:
            print(f"\n== {title}")
            for change in fn():
                print(f"   - {change}")

        if args.apply:
            print("\n== 校验")
            problems = verify(api, tenant, agent_id, guides)
            if problems:
                print("   校验失败：")
                for problem in problems:
                    print(f"   ✗ {problem}")
                return 1
            print(
                f"   ✓ 全部资产达到目标状态（{len(TOOLS)} 工具 schema 非空 / 人设 /"
                f" {len(SKILLS)} 技能 published / SOP 归档+未启用）"
            )

        if args.smoke_test:
            if not args.apply:
                print("\n--smoke-test 仅在 --apply 后有意义，跳过")
            else:
                print(f"\n== 冒烟测试（企业 {args.smoke_org}，账期 {period}，只读）")
                for line in smoke_test(api, tenant, agent_id, args.smoke_org, period):
                    print(f"   - {line}")
    except RuntimeError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1

    print(f"\n[{mode}] 完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
