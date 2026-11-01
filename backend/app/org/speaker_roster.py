"""本轮说话人的企业花名册只读解析。

解析键只有一条：同一租户内 ``EnterprisePerson.primary_user_id == User.id``。
企微私聊和网页对话框都先把说话人收成这个 User.id（见 turn_speaker），
这里不再用企微 userid、ChannelIdentity.display_name 或员工通讯录 Contact
去猜名字。Contact 挂在数字员工下，不参与企业组织。

查不到、姓名为空、或说话人未解析时，调用方不得编造姓名。
部门只在「今天恰好一个在职部门」时返回；多部门或已结束的任职不猜主部门。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.db.models import EnterprisePerson, OrgAffiliation, OrgDepartment

# 与组织任职同一日历。valid_from / valid_to 按上海日期闭区间判断。
_ORG_CALENDAR = ZoneInfo("Asia/Shanghai")
_LABEL_LIMIT = 80


@dataclass(frozen=True)
class SpeakerRoster:
    status: Literal["matched", "unmatched"]
    name: str = ""
    department: str = ""


def lookup_speaker_roster(db: Session, *, tenant_id: str, user_id: str) -> SpeakerRoster:
    """按登录账号查花名册。不读渠道外部 id，也不写库。"""

    tenant = str(tenant_id or "").strip()
    user = str(user_id or "").strip()
    if not tenant or not user:
        return SpeakerRoster(status="unmatched")
    person = db.exec(
        select(EnterprisePerson).where(
            EnterprisePerson.tenant_id == tenant,
            EnterprisePerson.primary_user_id == user,
        )
    ).first()
    if person is None:
        return SpeakerRoster(status="unmatched")
    name = _label(person.name)
    if not name:
        return SpeakerRoster(status="unmatched")
    return SpeakerRoster(
        status="matched",
        name=name,
        department=_current_department_name(db, tenant, person.id),
    )


def _current_department_name(db: Session, tenant_id: str, person_id: str) -> str:
    today = datetime.now(_ORG_CALENDAR).date()
    rows = db.exec(
        select(OrgAffiliation).where(
            OrgAffiliation.tenant_id == tenant_id,
            OrgAffiliation.person_id == person_id,
        )
    ).all()
    department_ids: list[str] = []
    for row in rows:
        if not _covers(row.valid_from, row.valid_to, today):
            continue
        department_id = str(row.department_id or "").strip()
        if department_id and department_id not in department_ids:
            department_ids.append(department_id)
    if len(department_ids) != 1:
        return ""
    department = db.get(OrgDepartment, department_ids[0])
    if department is None or department.tenant_id != tenant_id:
        return ""
    return _label(department.name)


def _covers(
    valid_from: date | datetime | str | None,
    valid_to: date | datetime | str | None,
    day: date,
) -> bool:
    start = _as_date(valid_from)
    end = _as_date(valid_to)
    if start is None or start > day:
        return False
    return end is None or end >= day


def _as_date(value: date | datetime | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _label(value: object, limit: int = _LABEL_LIMIT) -> str:
    return " ".join(str(value or "").split())[:limit]
