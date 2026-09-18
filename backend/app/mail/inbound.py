from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from sqlmodel import Session, col, or_, select

from app.db.models import (
    AgentProfile,
    ChannelBinding,
    ChannelBindingAgent,
    ChannelDelivery,
    ChannelIdentity,
    ChatSession,
    EmployeeMailbox,
    EmployeeMailMessage,
    EmployeeMailTriageRule,
    GeneralSkill,
    new_id,
    utc_now,
)
from app.mail.confirm import (
    skill_allows_inbound_auto_run,
    skill_inbound_match_hint,
)
from app.mail.errors import MailError
from app.mail.schema import MailComposeRequest, MailMessageRead, MailTeachRequest

logger = logging.getLogger(__name__)

DISPOSITION_SKILL = "skill"
DISPOSITION_ASK_OWNER = "ask_owner"
DISPOSITION_IGNORE = "ignore"
DISPOSITION_FAILED = "failed"

STATE_PENDING = "pending"
STATE_PROCESSING = "processing"
STATE_DONE = "done"
STATE_AWAITING_OWNER = "awaiting_owner"
STATE_FAILED = "failed"

MAIL_TRIAGE_NOTICE = "mail_triage_notice"
MAIL_TRIAGE_ACK = "mail_triage_ack"
MATCH_THRESHOLD = 0.6
INBOUND_MATCH_HINT_MAX = 200

_SKILL_RUNNER: Callable[..., None] | None = None

_SPAM_STRONG = (
    "unsubscribe",
    "退订",
    "click here to buy",
    "恭喜中奖",
    "免费领取",
    "viagra",
    "casino bonus",
    "limited-time offer",
    "你已中奖",
)
_SPAM_WEAK = ("优惠", "促销", "特价", "中奖", "免费", "lottery", "winner", "prize")

_ASK_AGAIN_PHRASES = ("下次仍问", "还是问我", "仍问我", "继续问我", "下次再问", "keep asking")
_IGNORE_PHRASES = ("这是垃圾", "以后同类忽略", "忽略", "垃圾", "spam", "junk", "ignore")


InboundSkillRunner = Callable[
    [Session, AgentProfile, GeneralSkill, EmployeeMailMessage],
    None,
]


def set_inbound_skill_runner(runner: InboundSkillRunner | None) -> None:
    global _SKILL_RUNNER
    _SKILL_RUNNER = runner


def mailbox_is_enabled(mailbox: EmployeeMailbox | None) -> bool:
    return mailbox is not None and bool(getattr(mailbox, "enabled", True))


def require_enabled_mailbox(mailbox: EmployeeMailbox) -> EmployeeMailbox:
    if not mailbox_is_enabled(mailbox):
        raise MailError(
            "MAIL_DISABLED",
            "该员工邮箱已停用，不能收取、发送或分流来信。",
        )
    return mailbox


def triage_label(row: EmployeeMailMessage) -> str | None:
    state = str(row.triage_state or "").strip()
    disposition = str(row.triage_disposition or "").strip()
    if state == STATE_PROCESSING:
        return "处理中"
    if disposition == DISPOSITION_SKILL:
        name = str(row.triage_skill_name or "").strip() or "技能"
        return f"已交技能「{name}」"
    if disposition == DISPOSITION_ASK_OWNER:
        if row.triage_notified_at is not None:
            return "已通知主人"
        return "待主人处理"
    if disposition == DISPOSITION_IGNORE:
        return "已忽略"
    if disposition == DISPOSITION_FAILED or state == STATE_FAILED:
        return "失败"
    if state == STATE_PENDING:
        return "待分流"
    return None


def fill_message_read(
    payload: MailMessageRead,
    row: EmployeeMailMessage,
    *,
    can_teach: bool = False,
) -> MailMessageRead:
    payload.triage_disposition = row.triage_disposition
    payload.triage_state = row.triage_state
    payload.triage_reason = row.triage_reason
    payload.triage_skill_id = row.triage_skill_id
    payload.triage_skill_name = row.triage_skill_name
    payload.triage_label = triage_label(row)
    payload.triage_notified = row.triage_notified_at is not None
    payload.can_teach = can_teach and row.folder == "inbox"
    payload.teaching_notice = str((row.metadata_json or {}).get("teaching_notice") or "") or None
    return payload


def pending_owner_count(db: Session, tenant_id: str, agent_id: str) -> int:
    rows = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == tenant_id,
            EmployeeMailMessage.agent_id == agent_id,
            EmployeeMailMessage.folder == "inbox",
            EmployeeMailMessage.triage_disposition == DISPOSITION_ASK_OWNER,
        )
    ).all()
    return len(rows)


def list_pending_owner_messages(
    db: Session, tenant_id: str, agent_id: str, *, limit: int = 50
) -> list[EmployeeMailMessage]:
    return list(
        db.exec(
            select(EmployeeMailMessage)
            .where(
                EmployeeMailMessage.tenant_id == tenant_id,
                EmployeeMailMessage.agent_id == agent_id,
                EmployeeMailMessage.folder == "inbox",
                EmployeeMailMessage.triage_disposition == DISPOSITION_ASK_OWNER,
            )
            .order_by(col(EmployeeMailMessage.received_at).desc(), col(EmployeeMailMessage.created_at).desc())
            .limit(limit)
        ).all()
    )


def list_inbound_opted_in_skills(db: Session, tenant_id: str, agent_id: str) -> list[GeneralSkill]:
    from app.core.capability_manifest import _visible_general_skills

    return [
        skill
        for skill in _visible_general_skills(db, tenant_id, agent_id)
        if skill.status == "published" and skill_allows_inbound_auto_run(skill)
    ]


def set_mailbox_enabled(
    db: Session, mailbox: EmployeeMailbox, *, enabled: bool
) -> EmployeeMailbox:
    mailbox.enabled = bool(enabled)
    mailbox.updated_at = utc_now()
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)
    return mailbox


def purge_agent_inbound_mail(db: Session, tenant_id: str, agent_id: str) -> None:
    rules = db.exec(
        select(EmployeeMailTriageRule).where(
            EmployeeMailTriageRule.tenant_id == tenant_id,
            EmployeeMailTriageRule.agent_id == agent_id,
        )
    ).all()
    for rule in rules:
        db.delete(rule)


def triage_all_pending(db: Session) -> None:
    mailboxes = db.exec(select(EmployeeMailbox)).all()
    for mailbox in mailboxes:
        if not mailbox_is_enabled(mailbox):
            continue
        try:
            triage_pending_for_mailbox(db, mailbox)
        except Exception:
            logger.exception(
                "来信分流失败 tenant=%s agent=%s", mailbox.tenant_id, mailbox.agent_id
            )


def triage_pending_for_mailbox(db: Session, mailbox: EmployeeMailbox) -> None:
    if not mailbox_is_enabled(mailbox):
        return
    rows = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == mailbox.tenant_id,
            EmployeeMailMessage.agent_id == mailbox.agent_id,
            EmployeeMailMessage.folder == "inbox",
            or_(
                col(EmployeeMailMessage.triage_state).is_(None),
                EmployeeMailMessage.triage_state == STATE_PENDING,
                col(EmployeeMailMessage.triage_disposition).is_(None),
            ),
        )
    ).all()
    for row in rows:
        if row.triage_disposition and row.triage_state not in {None, STATE_PENDING}:
            continue
        triage_message(db, mailbox, row)


def triage_message(
    db: Session,
    mailbox: EmployeeMailbox,
    message: EmployeeMailMessage,
) -> EmployeeMailMessage:
    if not mailbox_is_enabled(mailbox):
        return message
    if message.folder != "inbox":
        return message
    agent = db.get(AgentProfile, mailbox.agent_id)
    if agent is None or agent.tenant_id != mailbox.tenant_id:
        _apply_failure(db, message, "找不到该员工，无法分流来信。")
        return message
    message.triage_state = STATE_PROCESSING
    db.add(message)
    db.commit()
    db.refresh(message)

    rule = matching_teaching_rule(db, mailbox.tenant_id, mailbox.agent_id, message)
    if rule is not None:
        return _apply_teaching_rule(db, mailbox, agent, message, rule)

    opted = list_inbound_opted_in_skills(db, mailbox.tenant_id, mailbox.agent_id)
    matches = [skill for skill in opted if skill_match_score(skill, message) >= MATCH_THRESHOLD]
    if len(matches) == 1:
        return _hand_to_skill(
            db,
            mailbox,
            agent,
            message,
            matches[0],
            reason=f"来信对得上已开开关的技能「{matches[0].name}」。",
        )
    if len(matches) > 1:
        names = "、".join(skill.name for skill in matches)
        return ask_owner(
            db,
            mailbox,
            agent,
            message,
            reason=f"两个已开开关技能都像：{names}。请指定以后走哪一个。",
        )
    if high_confidence_spam(message):
        return ignore_message(
            db,
            message,
            reason="高置信判定为推销/无关来信，已忽略。",
        )
    if not opted:
        reason = "这个员工没有已开「可被来信自动跑」的已发布技能，且不像垃圾，需要主人决定。"
    else:
        reason = "没有已开开关技能能认领这封来信，也不像高置信垃圾，需要主人决定。"
    return ask_owner(db, mailbox, agent, message, reason=reason)


def skill_match_score(skill: GeneralSkill, message: EmployeeMailMessage) -> float:
    haystack = _haystack(message)
    hint = skill_inbound_match_hint(skill).strip().lower()
    name = str(skill.name or "").strip().lower()
    if hint and hint in haystack:
        return 1.0
    if hint:
        tokens = [token for token in re.split(r"[\s,，、/|]+", hint) if len(token) >= 2]
        if tokens:
            hits = sum(1 for token in tokens if token in haystack)
            return hits / len(tokens)
    if name and name in haystack:
        return 0.7
    description = str(skill.description or "").strip().lower()
    if description and description in haystack:
        return 0.55
    return 0.0


def high_confidence_spam(message: EmployeeMailMessage) -> bool:
    haystack = _haystack(message)
    if any(marker in haystack for marker in _SPAM_STRONG):
        return True
    weak_hits = sum(1 for marker in _SPAM_WEAK if marker in haystack)
    return weak_hits >= 3


def matching_teaching_rule(
    db: Session,
    tenant_id: str,
    agent_id: str,
    message: EmployeeMailMessage,
) -> EmployeeMailTriageRule | None:
    sender = _normalize_address(message.from_address)
    rules = db.exec(
        select(EmployeeMailTriageRule)
        .where(
            EmployeeMailTriageRule.tenant_id == tenant_id,
            EmployeeMailTriageRule.agent_id == agent_id,
        )
        .order_by(col(EmployeeMailTriageRule.updated_at).desc())
    ).all()
    exact = next((rule for rule in rules if rule.from_address == sender), None)
    if exact is not None:
        return exact
    return next((rule for rule in rules if rule.from_address == "*"), None)


def ignore_message(
    db: Session, message: EmployeeMailMessage, *, reason: str
) -> EmployeeMailMessage:
    message.triage_disposition = DISPOSITION_IGNORE
    message.triage_state = STATE_DONE
    message.triage_reason = reason
    message.updated_at = utc_now()
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def ask_owner(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    *,
    reason: str,
) -> EmployeeMailMessage:
    message.triage_disposition = DISPOSITION_ASK_OWNER
    message.triage_state = STATE_AWAITING_OWNER
    message.triage_reason = reason
    message.updated_at = utc_now()
    notified = notify_owner(db, mailbox, agent, message, reason=reason)
    if notified:
        message.triage_notified_at = utc_now()
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def teach_from_console(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    request: MailTeachRequest,
) -> EmployeeMailMessage:
    return apply_teaching(
        db,
        mailbox,
        agent,
        message,
        action=request.action,
        skill_id=request.skill_id,
        instruction=request.note,
        expand_all="所有" in (request.note or "") or "全部" in (request.note or ""),
    )


def apply_teaching(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    *,
    action: str,
    skill_id: str | None = None,
    instruction: str = "",
    expand_all: bool = False,
) -> EmployeeMailMessage:
    previous = str(message.triage_disposition or "")
    teaching_notice = ""
    if action == "ignore":
        ignore_message(
            db,
            message,
            reason=_teaching_reason(instruction) or "主人判定这是垃圾，已忽略。以后同类按此处理。",
        )
        if previous == DISPOSITION_SKILL:
            teaching_notice = "已按垃圾处理。已经发出的回信不会收回；以后同类改走忽略。"
        _upsert_rule(
            db,
            mailbox,
            message,
            disposition=DISPOSITION_IGNORE,
            instruction=instruction or "这是垃圾，以后同类忽略",
            expand_all=expand_all,
        )
    elif action == "ask_again":
        message.triage_disposition = DISPOSITION_ASK_OWNER
        message.triage_state = STATE_AWAITING_OWNER
        message.triage_reason = _teaching_reason(instruction) or "主人选择下次仍问我。"
        message.updated_at = utc_now()
        db.add(message)
        db.commit()
        db.refresh(message)
        _upsert_rule(
            db,
            mailbox,
            message,
            disposition=DISPOSITION_ASK_OWNER,
            instruction=instruction or "下次仍问我",
            expand_all=expand_all,
        )
    elif action == "skill":
        skill = _resolve_teach_skill(db, mailbox, skill_id, instruction)
        _upsert_rule(
            db,
            mailbox,
            message,
            disposition=DISPOSITION_SKILL,
            skill=skill,
            instruction=instruction or f"按技能「{skill.name}」处理",
            expand_all=expand_all,
        )
        _hand_to_skill(
            db,
            mailbox,
            agent,
            message,
            skill,
            reason=_teaching_reason(instruction)
            or f"主人指定按技能「{skill.name}」处理，已补跑该技能。",
        )
    else:
        raise MailError("MAIL_TEACH_INVALID", "教学去向只能是忽略、交技能或下次仍问我。")
    if teaching_notice:
        metadata = dict(message.metadata_json or {})
        metadata["teaching_notice"] = teaching_notice
        message.metadata_json = metadata
        db.add(message)
        db.commit()
        db.refresh(message)
    return message


def parse_owner_teaching(
    text: str, skills: list[GeneralSkill]
) -> tuple[str, GeneralSkill | None, bool] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()
    expand_all = "所有" in raw or "全部" in raw
    if any(phrase in raw or phrase in lowered for phrase in _ASK_AGAIN_PHRASES):
        return "ask_again", None, expand_all
    if any(phrase in raw or phrase in lowered for phrase in _IGNORE_PHRASES):
        return "ignore", None, expand_all
    for skill in skills:
        name = str(skill.name or "").strip()
        slug = str(skill.slug or "").strip()
        hint = skill_inbound_match_hint(skill)
        if name and (name in raw or f"按{name}" in raw or f"用{name}" in raw):
            return "skill", skill, expand_all
        if slug and slug in raw:
            return "skill", skill, expand_all
        if hint and hint in raw:
            return "skill", skill, expand_all
    return None


def notify_owner(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    *,
    reason: str,
    kind: str = MAIL_TRIAGE_NOTICE,
) -> bool:
    owner_id = str((agent.metadata_json or {}).get("owner_user_id") or "").strip()
    if not owner_id:
        return False
    text = _owner_notice_text(mailbox, agent, message, reason)
    _assert_no_secret(text, mailbox)
    bindings = list_agent_notify_bindings(db, mailbox.tenant_id, mailbox.agent_id)
    staged = False
    for binding in bindings:
        if _stage_owner_notice(
            db,
            binding,
            owner_id=owner_id,
            message=message,
            text=text,
            kind=kind,
        ):
            staged = True
    return staged


def list_agent_notify_bindings(
    db: Session, tenant_id: str, agent_id: str
) -> list[ChannelBinding]:
    mounts = db.exec(
        select(ChannelBindingAgent).where(ChannelBindingAgent.agent_id == agent_id)
    ).all()
    mount_ids = {row.binding_id for row in mounts}
    rows = db.exec(
        select(ChannelBinding).where(
            ChannelBinding.tenant_id == tenant_id,
            ChannelBinding.status == "active",
        )
    ).all()
    result: list[ChannelBinding] = []
    for row in rows:
        if row.team_id:
            continue
        if row.agent_id == agent_id or row.id in mount_ids:
            result.append(row)
    return result


def try_handle_channel_mail_teaching(
    db: Session,
    binding: ChannelBinding,
    inbound: Any,
) -> str | None:
    """Apply owner teaching when a channel reply quotes a mail-triage notice.

    Returns an ack text when handled, otherwise None.
    """
    reply_ids = _reply_message_ids(inbound)
    if not reply_ids:
        return None
    delivery = db.exec(
        select(ChannelDelivery).where(
            ChannelDelivery.tenant_id == binding.tenant_id,
            ChannelDelivery.binding_id == binding.id,
            col(ChannelDelivery.kind).in_((MAIL_TRIAGE_NOTICE, MAIL_TRIAGE_ACK)),
            col(ChannelDelivery.message_id).in_(reply_ids),
            ChannelDelivery.status == "delivered",
        )
    ).first()
    if delivery is None:
        return None
    target = delivery.target_json or {}
    expected = str(
        target.get("receive_id") or target.get("to_user_id") or ""
    ).strip()
    from_user = str(getattr(inbound, "from_user_id", "") or "").strip()
    if expected and from_user and expected != from_user:
        return None
    message_id = str(target.get("mail_message_id") or "").strip()
    if not message_id and str(delivery.session_id or "").startswith("mail-triage:"):
        message_id = str(delivery.session_id).split(":", 1)[1].strip()
    message = db.get(EmployeeMailMessage, message_id) if message_id else None
    if message is None or message.tenant_id != binding.tenant_id:
        return None
    mailbox = db.exec(
        select(EmployeeMailbox).where(
            EmployeeMailbox.tenant_id == message.tenant_id,
            EmployeeMailbox.agent_id == message.agent_id,
        )
    ).first()
    agent = db.get(AgentProfile, message.agent_id)
    if mailbox is None or agent is None:
        return "找不到对应的岗位邮箱，无法按回复教学。"
    owner_id = str((agent.metadata_json or {}).get("owner_user_id") or "").strip()
    identity = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.tenant_id == binding.tenant_id,
            ChannelIdentity.channel == binding.channel,
            ChannelIdentity.staffdeck_user_id == owner_id,
        )
    ).first() if owner_id else None
    if identity is None or str(identity.external_user_id or "") != from_user:
        return None
    skills = list_inbound_opted_in_skills(db, mailbox.tenant_id, mailbox.agent_id)
    parsed = parse_owner_teaching(str(getattr(inbound, "text", "") or ""), skills)
    if parsed is None:
        return "没看懂这次教学。请回复：这是垃圾 / 按某技能处理 / 下次仍问我。"
    action, skill, expand_all = parsed
    apply_teaching(
        db,
        mailbox,
        agent,
        message,
        action=action,
        skill_id=skill.id if skill is not None else None,
        instruction=str(getattr(inbound, "text", "") or ""),
        expand_all=expand_all,
    )
    if action == "ignore":
        return "已记下：这是垃圾，以后同类忽略。"
    if action == "ask_again":
        return "已记下：以后同类仍问你。"
    name = skill.name if skill is not None else "该技能"
    return f"已记下：以后同类按技能「{name}」处理，这封已补跑。"


def console_mail_path(agent_id: str, message_id: str) -> str:
    return (
        f"/enterprise/mail?agent_id={quote(agent_id)}"
        f"&message_id={quote(message_id)}"
    )


def _hand_to_skill(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    skill: GeneralSkill,
    *,
    reason: str,
) -> EmployeeMailMessage:
    if not skill_allows_inbound_auto_run(skill) or skill.status != "published":
        return ask_owner(
            db,
            mailbox,
            agent,
            message,
            reason="指定的技能未开「可被来信自动跑」或未发布，改为问主人。",
        )
    message.triage_disposition = DISPOSITION_SKILL
    message.triage_state = STATE_PROCESSING
    message.triage_skill_id = skill.id
    message.triage_skill_name = skill.name
    message.triage_reason = reason
    message.updated_at = utc_now()
    db.add(message)
    db.commit()
    db.refresh(message)
    try:
        _run_matched_skill(db, agent, skill, message)
    except MailError as exc:
        return _fail_skill(db, mailbox, agent, message, skill, exc.message)
    except Exception as exc:  # noqa: BLE001 - inbound skill must not pretend success.
        return _fail_skill(
            db,
            mailbox,
            agent,
            message,
            skill,
            f"技能「{skill.name}」执行失败：{exc.__class__.__name__}",
        )
    refreshed = db.get(EmployeeMailMessage, message.id) or message
    if refreshed.triage_disposition == DISPOSITION_FAILED:
        return refreshed
    failed_sent = db.exec(
        select(EmployeeMailMessage).where(
            EmployeeMailMessage.tenant_id == mailbox.tenant_id,
            EmployeeMailMessage.agent_id == mailbox.agent_id,
            EmployeeMailMessage.folder == "sent",
            EmployeeMailMessage.status == "failed",
            EmployeeMailMessage.in_reply_to == message.id,
        )
    ).first()
    if failed_sent is not None:
        return _fail_skill(
            db,
            mailbox,
            agent,
            refreshed,
            skill,
            failed_sent.smtp_error or "回信发出失败。",
        )
    refreshed.triage_disposition = DISPOSITION_SKILL
    refreshed.triage_state = STATE_DONE
    refreshed.triage_skill_id = skill.id
    refreshed.triage_skill_name = skill.name
    refreshed.triage_reason = reason
    refreshed.updated_at = utc_now()
    db.add(refreshed)
    db.commit()
    db.refresh(refreshed)
    return refreshed


def _fail_skill(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    skill: GeneralSkill,
    error: str,
) -> EmployeeMailMessage:
    message.triage_disposition = DISPOSITION_FAILED
    message.triage_state = STATE_FAILED
    message.triage_skill_id = skill.id
    message.triage_skill_name = skill.name
    message.triage_reason = f"技能「{skill.name}」未能做完：{error}"
    message.updated_at = utc_now()
    notified = notify_owner(
        db,
        mailbox,
        agent,
        message,
        reason=message.triage_reason,
        kind=MAIL_TRIAGE_NOTICE,
    )
    if notified:
        message.triage_notified_at = utc_now()
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _apply_failure(db: Session, message: EmployeeMailMessage, reason: str) -> EmployeeMailMessage:
    message.triage_disposition = DISPOSITION_FAILED
    message.triage_state = STATE_FAILED
    message.triage_reason = reason
    message.updated_at = utc_now()
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def _apply_teaching_rule(
    db: Session,
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    rule: EmployeeMailTriageRule,
) -> EmployeeMailMessage:
    if rule.disposition == DISPOSITION_IGNORE:
        return ignore_message(
            db,
            message,
            reason=rule.instruction or "按主人教过的规矩：同类来信忽略。",
        )
    if rule.disposition == DISPOSITION_ASK_OWNER:
        return ask_owner(
            db,
            mailbox,
            agent,
            message,
            reason=rule.instruction or "按主人教过的规矩：同类来信仍问主人。",
        )
    if rule.disposition == DISPOSITION_SKILL and rule.skill_id:
        skill = db.get(GeneralSkill, rule.skill_id)
        if skill is None or not skill_allows_inbound_auto_run(skill):
            return ask_owner(
                db,
                mailbox,
                agent,
                message,
                reason="主人教过走某技能，但该技能现在未开开关或已不存在，改问主人。",
            )
        return _hand_to_skill(
            db,
            mailbox,
            agent,
            message,
            skill,
            reason=rule.instruction or f"按主人教过的规矩交技能「{skill.name}」。",
        )
    return ask_owner(db, mailbox, agent, message, reason="教学规矩无法套用，改问主人。")


def _upsert_rule(
    db: Session,
    mailbox: EmployeeMailbox,
    message: EmployeeMailMessage,
    *,
    disposition: str,
    instruction: str,
    skill: GeneralSkill | None = None,
    expand_all: bool = False,
) -> EmployeeMailTriageRule:
    from_address = "*" if expand_all else _normalize_address(message.from_address)
    existing = db.exec(
        select(EmployeeMailTriageRule).where(
            EmployeeMailTriageRule.tenant_id == mailbox.tenant_id,
            EmployeeMailTriageRule.agent_id == mailbox.agent_id,
            EmployeeMailTriageRule.from_address == from_address,
        )
    ).first()
    if existing is None:
        existing = EmployeeMailTriageRule(
            tenant_id=mailbox.tenant_id,
            agent_id=mailbox.agent_id,
            from_address=from_address,
            disposition=disposition,
            instruction=instruction,
            source_message_id=message.id,
        )
    existing.disposition = disposition
    existing.instruction = instruction
    existing.source_message_id = message.id
    existing.skill_id = skill.id if skill is not None else None
    existing.skill_slug = skill.slug if skill is not None else None
    existing.updated_at = utc_now()
    db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


def _resolve_teach_skill(
    db: Session,
    mailbox: EmployeeMailbox,
    skill_id: str | None,
    instruction: str,
) -> GeneralSkill:
    opted = list_inbound_opted_in_skills(db, mailbox.tenant_id, mailbox.agent_id)
    if skill_id:
        for skill in opted:
            if skill.id == skill_id or skill.slug == skill_id:
                return skill
        raise MailError(
            "MAIL_TEACH_SKILL_NOT_ALLOWED",
            "只能交给已发布且已开「可被来信自动跑」的技能。",
        )
    parsed = parse_owner_teaching(instruction, opted)
    if parsed and parsed[0] == "skill" and parsed[1] is not None:
        return parsed[1]
    if len(opted) == 1:
        return opted[0]
    raise MailError("MAIL_TEACH_SKILL_REQUIRED", "请指定要交给哪一个已开开关的技能。")


def _run_matched_skill(
    db: Session,
    agent: AgentProfile,
    skill: GeneralSkill,
    message: EmployeeMailMessage,
) -> None:
    runner = _SKILL_RUNNER or _default_skill_runner
    runner(db, agent, skill, message)


def _default_skill_runner(
    db: Session,
    agent: AgentProfile,
    skill: GeneralSkill,
    message: EmployeeMailMessage,
) -> None:
    from app.core.agent_loop import AgentLoop
    from app.session.session_schema import ChatTurnRequest

    owner_id = str((agent.metadata_json or {}).get("owner_user_id") or "").strip() or None
    session = ChatSession(
        id=new_id("session"),
        tenant_id=agent.tenant_id,
        user_id=owner_id,
        agent_id=agent.id,
        title=f"来信 · {message.subject or message.id}",
        channel="mail_inbound",
    )
    db.add(session)
    db.commit()
    attachment_lines = []
    for item in message.attachments_json or []:
        path = str(item.get("cabinet_path") or "").strip()
        filename = str(item.get("filename") or "").strip()
        if path:
            attachment_lines.append(f"- {filename or path} → {path}")
    query = (
        f"/skill {skill.slug}\n\n"
        "这是岗位邮箱的一封来信。请按该技能做到岗位上应完成的结果。"
        "来信已命中已开「可被来信自动跑」的技能，回信、对外提交和 SMTP 发出都直接做完，"
        "不要再等人确认。\n"
        f"发件人：{message.from_address}\n"
        f"主题：{message.subject}\n"
        f"正文：\n{message.body_text}\n"
        f"附件：\n{chr(10).join(attachment_lines) or '（无）'}\n"
        f"邮件id：{message.id}\n"
    )
    AgentLoop(db).handle_turn(
        ChatTurnRequest(
            tenant_id=agent.tenant_id,
            session_id=session.id,
            agent_id=agent.id,
            user_id=owner_id,
            message=query,
            channel="mail_inbound",
            message_visibility="internal",
            inbound_mail_auto_complete=True,
        )
    )


def _stage_owner_notice(
    db: Session,
    binding: ChannelBinding,
    *,
    owner_id: str,
    message: EmployeeMailMessage,
    text: str,
    kind: str,
) -> bool:
    from app.channels.service_outbox import (
        _HANDOFF_NOTIFY_TARGET_BUILDERS,
        HANDOFF_NOTIFY_CHANNELS,
        resolve_assignee_channel_identity,
    )

    try:
        target: dict[str, Any] | None = None
        if binding.channel in HANDOFF_NOTIFY_CHANNELS:
            identity = resolve_assignee_channel_identity(db, binding, owner_id)
            builder = _HANDOFF_NOTIFY_TARGET_BUILDERS.get(binding.channel)
            if identity and identity.external_user_id and builder:
                target = builder(identity.external_user_id, message.id)
        if target is None:
            chat_session = db.exec(
                select(ChatSession)
                .where(
                    ChatSession.tenant_id == binding.tenant_id,
                    ChatSession.channel == binding.channel,
                    ChatSession.channel_binding_id == binding.id,
                    ChatSession.user_id == owner_id,
                    ChatSession.external_conv_id.is_not(None),
                )
                .order_by(ChatSession.updated_at.desc())
            ).first()
            if chat_session and (chat_session.channel_target_json or {}).get("to_user_id"):
                target = dict(chat_session.channel_target_json)
        if target is None:
            return False
        target["mail_message_id"] = message.id
        target["handoff_id"] = message.id
        db.add(
            ChannelDelivery(
                tenant_id=binding.tenant_id,
                binding_id=binding.id,
                session_id=f"mail-triage:{message.id}",
                message_id=None,
                target_json=target,
                kind=kind,
                text=text,
                status="pending",
                next_attempt_at=utc_now(),
                idempotency_key=new_id("mailnt"),
            )
        )
        db.commit()
        return True
    except Exception:
        logger.exception("来信问主人通知登记失败 binding=%s mail=%s", binding.id, message.id)
        db.rollback()
        return False


def _owner_notice_text(
    mailbox: EmployeeMailbox,
    agent: AgentProfile,
    message: EmployeeMailMessage,
    reason: str,
) -> str:
    path = console_mail_path(agent.id, message.id)
    lines = [
        f"员工「{agent.name}」有一封来信需要你决定怎么处理。",
        f"发件人：{message.from_address}",
        f"主题：{message.subject or '（无主题）'}",
        f"原因：{reason}",
        f"打开这封信：{path}",
        "直接回复这条消息即可教学：这是垃圾 / 按某技能处理 / 下次仍问我。",
    ]
    return "\n".join(lines)


def _reply_message_ids(inbound: Any) -> list[str]:
    ids: list[str] = []
    parent_id = str(getattr(inbound, "parent_id", "") or "").strip()
    if parent_id:
        ids.append(parent_id)
    raw = getattr(inbound, "raw", None) or {}
    message = raw.get("message") if isinstance(raw, dict) else None
    if isinstance(message, dict):
        root_id = str(message.get("root_id") or "").strip()
        if root_id and root_id not in ids:
            ids.append(root_id)
    return ids


def _haystack(message: EmployeeMailMessage) -> str:
    return "\n".join(
        [
            str(message.subject or ""),
            str(message.body_text or ""),
            str(message.from_address or ""),
        ]
    ).lower()


def _normalize_address(value: str) -> str:
    return str(value or "").strip().lower()


def _teaching_reason(instruction: str) -> str:
    text = str(instruction or "").strip()
    return text[:300] if text else ""


def _assert_no_secret(text: str, mailbox: EmployeeMailbox) -> None:
    secret = ""
    try:
        from app.security.encryption import decrypt_secret

        secret = decrypt_secret(mailbox.password_encrypted)
    except Exception:  # noqa: BLE001 - never leak decrypt failures into notices.
        secret = ""
    if secret and secret in text:
        raise MailError("MAIL_SECRET_LEAK", "通知内容不能包含邮箱密码。")


@dataclass
class InboundComposeInvoker:
    db: Session
    tenant_id: str
    agent_id: str
    inbound_mail_auto_complete: bool = True
    confirm_before_send_mail: bool = False
    active_skill: Any | None = None
    latest_user_text: str = ""
    _activated_names: set[str] | None = None
    session: Any | None = None


def inbound_compose_request(
    tenant_id: str,
    *,
    to: list[str],
    subject: str,
    body: str,
    in_reply_to: str | None = None,
) -> MailComposeRequest:
    return MailComposeRequest(
        tenant_id=tenant_id,
        to=to,
        subject=subject,
        body=body,
        in_reply_to=in_reply_to,
        source="inbound_skill",
    )
