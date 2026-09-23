"""Default Jev Choice questions for mail triage (editable in demo UI)."""

from __future__ import annotations

DEFAULT_CHOICE_CONFIG: dict = {
    "urgency": {
        "label": "紧急程度",
        "instructions": (
            "根据邮件内容判断紧急程度，只选一个选项。\n"
            "P0：账号冻结、付款截止今天、安全告警、老板/家人点名立刻回。\n"
            "P1：工作待办、会议变更今天/明天、客户明确要回复。\n"
            "P2：普通业务、订阅升级、可延后的问询。\n"
            "P3：通知类、已自动完成的回执、广告边缘但还不像垃圾。"
        ),
        "options": {
            "P0": "马上看",
            "P1": "今天处理",
            "P2": "本周",
            "P3": "备查",
        },
    },
    "spam": {
        "label": "是不是垃圾",
        "instructions": (
            "判断这封邮件是否为垃圾邮件。只选一个选项。\n"
            "银行、政府、已有往来客户，倾向 legit 或 unsure，宁可不标 spam。"
        ),
        "options": {
            "spam": "垃圾邮件",
            "legit": "正常邮件",
            "unsure": "不确定",
        },
    },
    "handling": {
        "label": "OpenClaw 能否自动处理",
        "instructions": (
            "判断 OpenClaw 能否自动处理（不涉及付钱、删数据、对外承诺、改密码）。\n"
            "auto：仅当 state 里 openclaw_skills 已有技能能做完。\n"
            "notify_only：需要人知道但不必立刻操作。\n"
            "ask_human：必须问主人。"
        ),
        "options": {
            "auto": "可自动处理",
            "notify_only": "只通知",
            "ask_human": "必须问人",
        },
    },
}


def choice_config_to_jev_questions(config: dict) -> dict:
    questions: dict = {}
    for qid, q in config.items():
        questions[qid] = {
            "type": "choice",
            "instructions": q.get("instructions") or "",
            "criteria": dict(q.get("options") or {}),
        }
    return questions
