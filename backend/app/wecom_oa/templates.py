"""按自建应用凭证读取可见审批模板，并写入员工 wecom_oa_approval 绑定。

企微公开文档只有 gettemplatedetail，没有全量模板目录。这里先尝试
`/oa/gettemplatelist`；该路径不可用时，再用受应用可见范围约束的
`/oa/getapprovalinfo` + `/oa/getapprovaldetail`，从近 30 天审批单抽出模板。
企微失败时返回空列表和可读错误，不把异常抛到页面。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from sqlmodel import Session, select

from app.db.models import AgentProfile, AgentResourceBinding
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
_CATALOG_MISSING_ERRCODES = {40058, 40066, 404, 48002}


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
    payload: dict[str, Any] = {
        "app_id": app.id,
        "app_name": app.name,
        "templates": [],
        "error": None,
        "notice": None,
        "assignments": list_template_assignments(db, tenant_id=tenant_id),
    }
    if app.status != "active":
        payload["error"] = "应用已停用，不能读取审批模板。"
        return payload
    try:
        client = WecomOaClient(app, client_factory=client_factory or _default_http_client)
        templates, notice = _load_from_wecom(client, now=now)
        payload["templates"] = templates
        payload["notice"] = notice
    except WecomOaCredentialError:
        logger.warning("wecom template secret unavailable app_id=%s", app.id)
        payload["error"] = "企微应用密钥无法解密，请检查服务端密钥配置"
    except WecomOaApiError as exc:
        logger.warning(
            "wecom template list failed app_id=%s errcode=%s code=%s",
            app.id,
            exc.errcode,
            exc.code,
        )
        payload["error"] = readable_template_error(exc)
    except Exception:
        logger.exception("wecom template list failed app_id=%s", app.id)
        payload["error"] = "读取企微审批模板失败"
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
    chosen: list[str] = []
    for raw_id in agent_ids:
        agent_id = str(raw_id or "").strip()
        if agent_id and agent_id not in chosen:
            chosen.append(agent_id)
    if not chosen:
        raise ValueError("请选择数字员工")
    if len(chosen) > _MAX_AGENTS:
        raise ValueError("一次最多分配 50 名数字员工")
    name = str(template_name or "").strip()[:128]
    agents: list[AgentProfile] = []
    for agent_id in chosen:
        agent = db.get(AgentProfile, agent_id)
        if agent is None or agent.tenant_id != tenant_id:
            raise ValueError("员工不存在")
        agents.append(agent)
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
                "scenario_key": scenario_key,
            }
        )
    return results


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
                "scenario_key": str(meta.get("scenario_key") or binding.resource_id or "default"),
            }
        )
    assignments.sort(key=lambda item: (item["template_id"], item["agent_name"], item["agent_id"]))
    return assignments


def readable_template_error(exc: WecomOaApiError) -> str:
    if exc.errcode == 301055 or exc.code == "WECOM_OA_NO_PERMISSION":
        return "当前应用没有审批接口权限。请在企微后台把该应用加入「审批 - 可调用接口的应用」。"
    if exc.errcode == 301025 or exc.code == "WECOM_OA_TEMPLATE_NOT_FOUND":
        return "企微未找到该审批模板，或当前应用无权查看。"
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
    return f"读取企微审批模板失败：{text or '未知错误'}"


def _load_from_wecom(
    client: WecomOaClient, *, now: int | None
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        catalog = client.list_template_catalog()
    except WecomOaApiError as exc:
        if not _catalog_missing(exc):
            raise
        logger.info(
            "wecom template catalog unavailable errcode=%s; discovering from approvals",
            exc.errcode,
        )
        return _discover_from_approvals(client, now=now)
    templates = _enrich_catalog_names(client, _parse_catalog(catalog))
    if not templates:
        return [], "企微未返回可见的审批模板。"
    return templates, None


def _catalog_missing(exc: WecomOaApiError) -> bool:
    if exc.errcode == 301055 or exc.code == "WECOM_OA_NO_PERMISSION":
        return False
    if exc.errcode in _CATALOG_MISSING_ERRCODES:
        return True
    message = f"{exc.errmsg or ''} {exc}".lower()
    return any(
        token in message
        for token in ("invalid url", "api not exist", "api forbidden")
    )


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
    notice = (
        "企微没有公开的全量模板目录。下列模板来自该应用可见范围内近 30 天的审批单，"
        "只包含这段时间内实际提交过的模板。"
    )
    if truncated:
        notice += "列表可能不完整。"
    if not templates:
        notice = "近 30 天内，该应用可见范围没有审批单，所以没有可列出的模板。可粘贴模板 ID 核对。"
    return templates, notice


def _enrich_catalog_names(
    client: WecomOaClient, templates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """目录若只给了 template_id，再用 gettemplatedetail 补中文名。失败则保留原行。"""
    enriched: list[dict[str, Any]] = []
    for index, row in enumerate(templates):
        if index >= 20 or row["name"] != row["template_id"]:
            enriched.append(row)
            continue
        try:
            detail = client.get_template_detail(row["template_id"])
        except WecomOaApiError:
            enriched.append(row)
            continue
        enriched.append(
            _template_from_detail(row["template_id"], detail, source=str(row["source"]))
        )
    return enriched


def _parse_catalog(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("template_list")
    if raw is None:
        raw = payload.get("templates")
    if isinstance(raw, dict):
        raw = raw.get("template_list") or raw.get("list") or []
    if not isinstance(raw, list):
        return []
    templates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            template_id = item.strip()
            name = template_id
            control_count = None
        elif isinstance(item, dict):
            template_id = str(item.get("template_id") or item.get("id") or "").strip()
            name = _pick_name(item, fallback=template_id)
            control_count = _control_count(item)
        else:
            continue
        if not template_id or template_id in seen:
            continue
        seen.add(template_id)
        templates.append(
            _public_template(template_id, name, source="catalog", control_count=control_count)
        )
    return templates


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
