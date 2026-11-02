"""按自建应用凭证管理办公审批模板，并写入员工 wecom_oa_approval 绑定。

企微没有可用的全量模板目录，本模块不调用 `/oa/gettemplatelist`。
模板来自三处：管理员核对后保存的记录、已分配给数字员工的绑定，
以及该应用可见范围内近 30 天审批单里出现过的 template_id。
企微发现失败时仍返回已保存和已分配的模板。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from sqlmodel import Session, select

from app.db.models import (
    AgentProfile,
    AgentResourceBinding,
    WecomSavedApprovalTemplate,
    utc_now,
)
from app.wecom_oa.apps import get_wecom_app
from app.wecom_oa.client import WecomOaApiError, WecomOaClient
from app.wecom_oa.credentials import WecomOaCredentialError
from app.wecom_oa.policy import RESOURCE_TYPE, WecomOaConfigError, validate_approval_config
from app.wecom_oa.service import (
    _scenario_key,
    _stored_approval_metadata,
    _upsert_approval_binding,
)

logger = logging.getLogger(__name__)

LOOKBACK_SECONDS = 30 * 24 * 3600
APPROVAL_PAGE_SIZE = 100
APPROVAL_PAGE_CAP = 2
DETAIL_CAP = 20


def _default_http_client() -> httpx.Client:
    return httpx.Client(timeout=8.0)


_MAX_TEMPLATE_ID_LEN = 128
_MAX_AGENTS = 50
_ENRICH_CAP = 20


class WecomTemplateError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def load_visible_approval_templates(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    client_factory: Callable[[], httpx.Client] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise WecomTemplateError("企微应用不存在", status_code=404)
    saved = list_saved_approval_templates(db, tenant_id=tenant_id, app_id=app.id)
    assignments = list_template_assignments(db, tenant_id=tenant_id)
    payload: dict[str, Any] = {
        "app_id": app.id,
        "app_name": app.name,
        "templates": _merge_templates(saved, [], assignments),
        "error": None,
        "notice": None,
        "assignments": assignments,
    }
    if app.status != "active":
        payload["error"] = "应用已停用，不能读取或核对新的审批模板。"
        return payload
    try:
        client = WecomOaClient(app, client_factory=client_factory or _default_http_client)
        discovered, notice = _discover_from_approvals(client, now=now)
        merged = _merge_templates(saved, discovered, assignments)
        payload["templates"] = _enrich_missing_names(client, merged)
        payload["notice"] = notice
    except WecomOaCredentialError:
        logger.warning("wecom template secret unavailable app_id=%s", app.id)
        payload["error"] = "企微应用密钥无法解密，请检查服务端密钥配置"
    except WecomOaApiError as exc:
        logger.warning(
            "wecom template discovery failed app_id=%s errcode=%s code=%s",
            app.id,
            exc.errcode,
            exc.code,
        )
        payload["notice"] = readable_discovery_notice(exc)
    except Exception:
        logger.exception("wecom template discovery failed app_id=%s", app.id)
        payload["notice"] = readable_discovery_notice(None)
    return payload


def lookup_approval_template(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    template_id: str,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> dict[str, Any]:
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise WecomTemplateError("企微应用不存在", status_code=404)
    template_id = str(template_id or "").strip()
    if not template_id:
        return {"template": None, "error": "请填写模板 ID"}
    if len(template_id) > _MAX_TEMPLATE_ID_LEN:
        return {"template": None, "error": "template_id 无效"}
    if app.status != "active":
        return {"template": None, "error": "应用已停用，不能核对审批模板。"}
    try:
        client = WecomOaClient(app, client_factory=client_factory or _default_http_client)
        detail = client.get_template_detail(template_id)
    except WecomOaCredentialError:
        return {"template": None, "error": "企微应用密钥无法解密，请检查服务端密钥配置"}
    except WecomOaApiError as exc:
        logger.warning(
            "wecom template lookup failed app_id=%s errcode=%s",
            app.id,
            exc.errcode,
        )
        return {"template": None, "error": readable_template_error(exc)}
    except Exception:
        logger.exception("wecom template lookup failed app_id=%s", app.id)
        return {"template": None, "error": "读取企微审批模板失败"}
    return {
        "template": _template_from_detail(template_id, detail, source="lookup"),
        "error": None,
    }


def save_approval_template(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    template_id: str,
    client_factory: Callable[[], httpx.Client] | None = None,
    created_by_user_id: str | None = None,
) -> dict[str, Any]:
    """用 gettemplatedetail 核对成功后写入库。刷新页面仍能看到。"""
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise WecomTemplateError("企微应用不存在", status_code=404)
    template_id = str(template_id or "").strip()
    if not template_id or len(template_id) > _MAX_TEMPLATE_ID_LEN:
        return {"template": None, "error": "template_id 无效" if template_id else "请填写模板 ID"}
    if app.status != "active":
        return {"template": None, "error": "应用已停用，不能核对审批模板。"}
    try:
        client = WecomOaClient(app, client_factory=client_factory or _default_http_client)
        detail = client.get_template_detail(template_id)
    except WecomOaCredentialError:
        return {"template": None, "error": "企微应用密钥无法解密，请检查服务端密钥配置"}
    except WecomOaApiError as exc:
        logger.warning(
            "wecom template save failed app_id=%s errcode=%s",
            app.id,
            exc.errcode,
        )
        return {"template": None, "error": readable_template_error(exc)}
    except Exception:
        logger.exception("wecom template save failed app_id=%s", app.id)
        return {"template": None, "error": "读取企微审批模板失败"}
    template = _template_from_detail(template_id, detail, source="saved")
    row = _find_saved_template(
        db, tenant_id=tenant_id, app_id=app.id, template_id=template_id
    )
    if row is None:
        row = WecomSavedApprovalTemplate(
            tenant_id=tenant_id,
            app_id=app.id,
            template_id=template_id,
            name=template["name"],
            control_count=template["control_count"],
            created_by_user_id=created_by_user_id,
        )
        db.add(row)
    else:
        row.name = template["name"]
        row.control_count = template["control_count"]
        row.updated_at = utc_now()
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"template": template, "error": None}


def delete_saved_approval_template(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    template_id: str,
) -> None:
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise WecomTemplateError("企微应用不存在", status_code=404)
    template_id = str(template_id or "").strip()
    row = _find_saved_template(
        db, tenant_id=tenant_id, app_id=app.id, template_id=template_id
    )
    if row is None:
        raise WecomTemplateError("未保存该审批模板", status_code=404)
    db.delete(row)
    db.commit()


def assign_approval_template(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    template_id: str,
    agent_ids: list[str],
    template_name: str | None = None,
    scenario_key: str = "default",
) -> list[dict[str, Any]]:
    """把一个模板写到多名员工的默认场景。每名员工该场景只保留这一个 template_id。"""
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise ValueError("企微应用不存在")
    template_id = str(template_id or "").strip()
    if not template_id or len(template_id) > _MAX_TEMPLATE_ID_LEN:
        raise ValueError("template_id 无效")
    scenario_key = _scenario_key(scenario_key)
    chosen = _unique_agent_ids(agent_ids)
    if not chosen:
        raise ValueError("请选择数字员工")
    if len(chosen) > _MAX_AGENTS:
        raise ValueError("一次最多分配 50 名数字员工")
    name = str(template_name or "").strip()[:128]
    agents = _require_agents(db, tenant_id=tenant_id, agent_ids=chosen)
    results: list[dict[str, Any]] = []
    for agent in agents:
        meta = dict(
            _stored_approval_metadata(
                db,
                tenant_id=tenant_id,
                agent_id=agent.id,
                scenario_key=scenario_key,
            )
            or {}
        )
        meta["template_id"] = template_id
        meta["scenario_key"] = scenario_key
        if name:
            meta["template_name"] = name
        if not str(meta.get("applicant_mode") or "").strip():
            meta["applicant_mode"] = "fixed"
        if not isinstance(meta.get("field_mappings"), list):
            meta["field_mappings"] = []
        if name and not str(meta.get("display_name") or "").strip():
            meta["display_name"] = name
        try:
            stored = validate_approval_config(meta)
        except WecomOaConfigError:
            stored = meta
        if name:
            stored["template_name"] = name
        row = _upsert_approval_binding(
            db,
            tenant_id=tenant_id,
            agent_id=agent.id,
            scenario_key=scenario_key,
            metadata=stored,
        )
        saved = dict(row.metadata_json or {})
        results.append(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "template_id": str(saved.get("template_id") or ""),
                "template_name": str(saved.get("template_name") or ""),
                "scenario_key": scenario_key,
            }
        )
    return results


def unassign_approval_template(
    db: Session,
    *,
    tenant_id: str,
    app_id: str,
    template_id: str,
    agent_ids: list[str],
    scenario_key: str = "default",
) -> list[dict[str, Any]]:
    """取消员工与该模板的绑定。保留申请人 userid 等其余配置。"""
    app = get_wecom_app(db, tenant_id=tenant_id, app_id=app_id)
    if app is None:
        raise ValueError("企微应用不存在")
    template_id = str(template_id or "").strip()
    if not template_id or len(template_id) > _MAX_TEMPLATE_ID_LEN:
        raise ValueError("template_id 无效")
    scenario_key = _scenario_key(scenario_key)
    chosen = _unique_agent_ids(agent_ids)
    if not chosen:
        raise ValueError("请选择数字员工")
    agents = _require_agents(db, tenant_id=tenant_id, agent_ids=chosen)
    rows: list[tuple[AgentProfile, AgentResourceBinding]] = []
    for agent in agents:
        binding = db.exec(
            select(AgentResourceBinding).where(
                AgentResourceBinding.tenant_id == tenant_id,
                AgentResourceBinding.agent_id == agent.id,
                AgentResourceBinding.resource_type == RESOURCE_TYPE,
                AgentResourceBinding.resource_id == scenario_key,
                AgentResourceBinding.status == "active",
            )
        ).first()
        meta = dict(binding.metadata_json or {}) if binding is not None else {}
        if binding is None or str(meta.get("template_id") or "").strip() != template_id:
            raise ValueError("该员工未分配此模板")
        rows.append((agent, binding))
    results: list[dict[str, Any]] = []
    for agent, binding in rows:
        meta = dict(binding.metadata_json or {})
        meta["template_id"] = ""
        meta.pop("template_name", None)
        binding.metadata_json = meta
        binding.updated_at = utc_now()
        db.add(binding)
        results.append(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "template_id": "",
                "scenario_key": scenario_key,
            }
        )
    db.commit()
    return results


def list_saved_approval_templates(
    db: Session, *, tenant_id: str, app_id: str
) -> list[dict[str, Any]]:
    rows = db.exec(
        select(WecomSavedApprovalTemplate).where(
            WecomSavedApprovalTemplate.tenant_id == tenant_id,
            WecomSavedApprovalTemplate.app_id == app_id,
        )
    ).all()
    templates = [
        _public_template(
            row.template_id,
            row.name,
            source="saved",
            control_count=row.control_count,
        )
        for row in rows
    ]
    templates.sort(key=lambda item: (item["name"], item["template_id"]))
    return templates


def list_template_assignments(db: Session, *, tenant_id: str) -> list[dict[str, Any]]:
    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.resource_type == RESOURCE_TYPE,
            AgentResourceBinding.status == "active",
        )
    ).all()
    agents = {
        row.id: row
        for row in db.exec(
            select(AgentProfile).where(AgentProfile.tenant_id == tenant_id)
        ).all()
    }
    assignments: list[dict[str, Any]] = []
    for binding in bindings:
        meta = binding.metadata_json or {}
        template_id = str(meta.get("template_id") or "").strip()
        agent = agents.get(binding.agent_id)
        if not template_id or agent is None:
            continue
        assignments.append(
            {
                "agent_id": agent.id,
                "agent_name": agent.name,
                "template_id": template_id,
                "template_name": str(meta.get("template_name") or "").strip(),
                "scenario_key": str(meta.get("scenario_key") or binding.resource_id or "default"),
            }
        )
    assignments.sort(key=lambda item: (item["template_id"], item["agent_name"], item["agent_id"]))
    return assignments


def readable_template_error(exc: WecomOaApiError) -> str:
    if exc.errcode == 301055 or exc.code == "WECOM_OA_NO_PERMISSION":
        return "当前应用没有审批接口权限。请在企微后台把该应用加入「审批 - 可调用接口的应用」。"
    if exc.errcode == 301025 or exc.code == "WECOM_OA_TEMPLATE_NOT_FOUND":
        return (
            "企微未找到该审批模板（301025）。"
            "请使用办公「审批」系统应用里的模板 ID。"
            "自建应用「审批接口」编辑页上的 ID 不能用于核对或代发。"
        )
    if exc.errcode == 60011 or exc.code == "WECOM_OA_VISIBLE_SCOPE":
        return "该数据不在当前应用的可见范围内。"
    if exc.errcode == 48002:
        return "当前应用未被允许调用该企微接口。请确认它已加入「审批 - 可调用接口的应用」。"
    if exc.code in {
        "WECOM_OA_TOKEN_FAILED",
        "WECOM_OA_TOKEN_INVALID",
        "WECOM_OA_INVALID_ACCESS_TOKEN",
        "WECOM_OA_ACCESS_TOKEN_EXPIRED",
    }:
        return "企微访问令牌获取失败，请检查该应用的企业 ID 与 Secret。"
    text = str(exc.errmsg or exc).strip()
    lowered = text.lower()
    if "gettemplatelist" in lowered:
        return "读取企微审批模板失败"
    return f"读取企微审批模板失败：{text or '未知错误'}"


def readable_discovery_notice(exc: WecomOaApiError | None) -> str:
    if exc is not None and (
        exc.errcode in {301055, 48002} or exc.code == "WECOM_OA_NO_PERMISSION"
    ):
        return (
            "当前应用没有审批接口权限，读不到近期审批单。"
            "已保存和已分配的模板仍可管理。"
            "请在企微后台把该应用加入「审批 - 可调用接口的应用」。"
        )
    return "近期审批单暂时读不到。已保存和已分配的模板仍可管理，也可粘贴模板 ID 核对并保存。"


def _find_saved_template(
    db: Session, *, tenant_id: str, app_id: str, template_id: str
) -> WecomSavedApprovalTemplate | None:
    if not template_id:
        return None
    return db.exec(
        select(WecomSavedApprovalTemplate).where(
            WecomSavedApprovalTemplate.tenant_id == tenant_id,
            WecomSavedApprovalTemplate.app_id == app_id,
            WecomSavedApprovalTemplate.template_id == template_id,
        )
    ).first()


def _unique_agent_ids(agent_ids: list[str]) -> list[str]:
    chosen: list[str] = []
    for raw_id in agent_ids:
        agent_id = str(raw_id or "").strip()
        if agent_id and agent_id not in chosen:
            chosen.append(agent_id)
    return chosen


def _require_agents(
    db: Session, *, tenant_id: str, agent_ids: list[str]
) -> list[AgentProfile]:
    agents: list[AgentProfile] = []
    for agent_id in agent_ids:
        agent = db.get(AgentProfile, agent_id)
        if agent is None or agent.tenant_id != tenant_id:
            raise ValueError("员工不存在")
        agents.append(agent)
    return agents


def _merge_templates(
    saved: list[dict[str, Any]],
    discovered: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for row in saved:
        rows[row["template_id"]] = dict(row)
    for row in discovered:
        if row["template_id"] not in rows:
            rows[row["template_id"]] = dict(row)
    names = _assignment_template_names(assignments)
    for template_id, name in names.items():
        if template_id not in rows:
            rows[template_id] = _public_template(
                template_id, name, source="assignment", control_count=None
            )
    for item in assignments:
        template_id = item["template_id"]
        if template_id not in rows:
            rows[template_id] = _public_template(
                template_id,
                template_id,
                source="assignment",
                control_count=None,
            )
    return list(rows.values())


def _assignment_template_names(assignments: list[dict[str, Any]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for item in assignments:
        template_id = str(item.get("template_id") or "").strip()
        candidate = str(item.get("template_name") or "").strip()
        if template_id and candidate and template_id not in names:
            names[template_id] = candidate
    return names


def _discover_from_approvals(
    client: WecomOaClient, *, now: int | None
) -> tuple[list[dict[str, Any]], str | None]:
    endtime = int(now if now is not None else time.time())
    starttime = endtime - LOOKBACK_SECONDS
    cursor = ""
    sp_nos: list[str] = []
    truncated = False
    for _ in range(APPROVAL_PAGE_CAP):
        page = client.list_approval_info(
            starttime=starttime,
            endtime=endtime,
            cursor=cursor,
            size=APPROVAL_PAGE_SIZE,
        )
        raw = page.get("sp_no_list") or []
        if isinstance(raw, list):
            for item in raw:
                sp_no = str(item or "").strip()
                if sp_no and sp_no not in sp_nos:
                    sp_nos.append(sp_no)
        cursor = str(page.get("new_next_cursor") or "").strip()
        if not cursor:
            break
    else:
        truncated = True
    if cursor:
        truncated = True
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for sp_no in sp_nos[:DETAIL_CAP]:
        try:
            detail = client.get_approval_detail(sp_no)
        except WecomOaApiError:
            logger.info("skip approval detail sp_no=%s", sp_no)
            continue
        info = detail.get("info") if isinstance(detail.get("info"), dict) else {}
        template_id = str(info.get("template_id") or "").strip()
        if not template_id or template_id in seen:
            continue
        seen.add(template_id)
        name = _pick_name(info, fallback=template_id)
        templates.append(
            _public_template(template_id, name, source="approval", control_count=None)
        )
    if len(sp_nos) > DETAIL_CAP:
        truncated = True
    if not templates:
        return [], None
    notice = (
        "下列「近 30 天审批」来自该应用可见范围内的审批单，"
        "只包含这段时间内实际提交过的模板。已保存和已分配的模板会一直显示。"
    )
    if truncated:
        notice += "列表可能不完整。"
    return templates, notice


def _enrich_missing_names(
    client: WecomOaClient, templates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """已分配或审批单里只有 template_id 时，用 gettemplatedetail 补中文名。"""
    enriched: list[dict[str, Any]] = []
    looked_up = 0
    for row in templates:
        if looked_up >= _ENRICH_CAP or row["name"] != row["template_id"]:
            enriched.append(row)
            continue
        if row["source"] == "saved":
            enriched.append(row)
            continue
        looked_up += 1
        try:
            detail = client.get_template_detail(row["template_id"])
        except WecomOaApiError:
            enriched.append(row)
            continue
        enriched.append(
            _template_from_detail(row["template_id"], detail, source=str(row["source"]))
        )
    return enriched


def _template_from_detail(
    template_id: str, detail: dict[str, Any], *, source: str
) -> dict[str, Any]:
    return _public_template(
        template_id,
        _pick_name(detail, fallback=template_id),
        source=source,
        control_count=_control_count(detail),
    )


def _public_template(
    template_id: str,
    name: str,
    *,
    source: str,
    control_count: int | None,
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "name": name or template_id,
        "control_count": control_count,
        "source": source,
    }


def _pick_name(item: dict[str, Any], *, fallback: str) -> str:
    for key in ("template_names", "template_name"):
        found = _name_from_value(item.get(key))
        if found:
            return found
    for key in ("name", "sp_name"):
        text = str(item.get(key) or "").strip()
        if text:
            return text
    return fallback


def _name_from_value(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if not isinstance(value, list):
        return ""
    zh = ""
    first = ""
    for item in value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if not first:
            first = text
        if str(item.get("lang") or "") == "zh_CN":
            zh = text
            break
    return zh or first


def _control_count(item: dict[str, Any]) -> int | None:
    content = item.get("template_content")
    if not isinstance(content, dict):
        return None
    controls = content.get("controls")
    if not isinstance(controls, list):
        return None
    return len(controls)
