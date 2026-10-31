"""用默认企微自建应用只读拉取可见部门与部门成员。

不落库。手机号、地址等通讯录敏感字段不返回。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx
from sqlmodel import Session

from app.channels.adapters.wecom import WECOM_API_BASE
from app.wecom_oa.apps import get_default_wecom_app
from app.wecom_oa.client import WecomOaApiError, WecomOaTokenProvider
from app.wecom_oa.credentials import WecomOaCredentialError, load_plaintext_secrets

logger = logging.getLogger(__name__)

_SCOPE_ERRCODES = {60011, 48002}
_DEPARTMENT_ERRCODES = {60003, 60123}
_TOKEN_RETRY_ERRCODES = {40014, 42001}
_NAME_LIMIT = 128
_EMAIL_LIMIT = 256
_USERID_LIMIT = 128


class WecomDirectoryError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 502,
        code: str = "WECOM_DIRECTORY_ERROR",
        errcode: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.errcode = errcode


class WecomDirectoryClient:
    """department/list 与 user/list。复用 OA token 缓存，过期刷新一次。"""

    def __init__(
        self,
        credential: Any,
        *,
        token_provider: WecomOaTokenProvider | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ):
        self.credential = credential
        factory = client_factory or (lambda: httpx.Client(timeout=15.0))
        self._client_factory = factory
        self._tokens = token_provider or WecomOaTokenProvider(client_factory=factory)

    def list_departments(self) -> list[dict[str, Any]]:
        payload = self._get("/department/list", {})
        raw = payload.get("department") or []
        if not isinstance(raw, list):
            raise WecomDirectoryError(
                "企微通讯录请求失败",
                code="WECOM_DIRECTORY_REQUEST_FAILED",
            )
        departments = [row for item in raw if (row := _public_department(item))]
        if not departments:
            raise WecomDirectoryError(
                "企微应用可见范围为空",
                status_code=409,
                code="WECOM_DIRECTORY_EMPTY_SCOPE",
            )
        departments.sort(key=lambda row: (-int(row["order"]), int(row["id"])))
        return departments

    def list_users(self, department_id: int) -> list[dict[str, Any]]:
        if department_id <= 0:
            raise WecomDirectoryError(
                "部门编号无效",
                status_code=400,
                code="WECOM_DIRECTORY_BAD_DEPARTMENT",
            )
        payload = self._get(
            "/user/list",
            {"department_id": str(department_id), "fetch_child": "0"},
        )
        raw = payload.get("userlist") or []
        if not isinstance(raw, list):
            raise WecomDirectoryError(
                "企微通讯录请求失败",
                code="WECOM_DIRECTORY_REQUEST_FAILED",
            )
        users: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            row = _public_user(item)
            if row is None or row["userid"] in seen:
                continue
            seen.add(row["userid"])
            users.append(row)
        users.sort(key=lambda row: (row["name"], row["userid"]))
        return users

    def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        token = self._token()
        try:
            return self._request(path, token, params)
        except WecomDirectoryError as exc:
            if exc.errcode not in _TOKEN_RETRY_ERRCODES:
                raise
        self._tokens.invalidate(self.credential)
        token = self._token(force_refresh=True)
        return self._request(path, token, params)

    def _token(self, *, force_refresh: bool = False) -> str:
        try:
            return self._tokens.get(self.credential, force_refresh=force_refresh)
        except WecomOaApiError as exc:
            errcode = exc.errcode
            suffix = f"（errcode={errcode}）" if errcode is not None and errcode >= 0 else ""
            raise WecomDirectoryError(
                f"企微 access_token 获取失败{suffix}",
                status_code=502,
                code="WECOM_DIRECTORY_TOKEN_FAILED",
                errcode=errcode,
            ) from None

    def _request(self, path: str, token: str, params: dict[str, str]) -> dict[str, Any]:
        query = {"access_token": token, **params}
        try:
            with self._client_factory() as client:
                response = client.get(f"{WECOM_API_BASE}{path}", params=query)
                data = response.json()
        except (httpx.HTTPError, ValueError, TypeError):
            logger.warning("wecom directory http failed path=%s", path)
            raise WecomDirectoryError(
                "企微通讯录请求失败",
                code="WECOM_DIRECTORY_REQUEST_FAILED",
            ) from None
        if not isinstance(data, dict):
            raise WecomDirectoryError(
                "企微通讯录请求失败",
                code="WECOM_DIRECTORY_REQUEST_FAILED",
            )
        errcode = _errcode(data)
        if response.status_code >= 400 or errcode != 0:
            raise _api_error(path, errcode)
        return data


def load_department_tree(
    db: Session,
    *,
    tenant_id: str,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> dict[str, Any]:
    app = _require_default_app(db, tenant_id)
    departments = _client(app, client_factory).list_departments()
    return {
        "app_id": app.id,
        "app_name": app.name,
        "corp_id": app.corp_id,
        "departments": departments,
    }


def load_department_users(
    db: Session,
    *,
    tenant_id: str,
    department_id: int,
    client_factory: Callable[[], httpx.Client] | None = None,
) -> dict[str, Any]:
    app = _require_default_app(db, tenant_id)
    users = _client(app, client_factory).list_users(int(department_id))
    return {"department_id": int(department_id), "users": users}


def _require_default_app(db: Session, tenant_id: str) -> Any:
    app = get_default_wecom_app(db, tenant_id=tenant_id)
    if app is None:
        raise WecomDirectoryError(
            "未配置可用的默认企微应用",
            status_code=404,
            code="WECOM_DIRECTORY_NO_DEFAULT_APP",
        )
    return app


def _client(app: Any, client_factory: Callable[[], httpx.Client] | None) -> WecomDirectoryClient:
    try:
        load_plaintext_secrets(app)
    except WecomOaCredentialError as exc:
        raise WecomDirectoryError(
            str(exc),
            status_code=500,
            code="WECOM_DIRECTORY_SECRET",
        ) from None
    return WecomDirectoryClient(app, client_factory=client_factory)


def _public_department(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    try:
        department_id = int(item.get("id"))
    except (TypeError, ValueError):
        return None
    if department_id <= 0:
        return None
    try:
        parent_id = int(item.get("parentid") or 0)
    except (TypeError, ValueError):
        parent_id = 0
    try:
        order = int(item.get("order") or 0)
    except (TypeError, ValueError):
        order = 0
    name = str(item.get("name") or "").strip()[:_NAME_LIMIT] or str(department_id)
    return {
        "id": department_id,
        "name": name,
        "parent_id": parent_id,
        "order": order,
    }


def _public_user(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    userid = str(item.get("userid") or "").strip()[:_USERID_LIMIT]
    if not userid:
        return None
    name = str(item.get("name") or "").strip()[:_NAME_LIMIT] or userid
    email = str(item.get("email") or "").strip()
    if not email:
        email = str(item.get("biz_mail") or "").strip()
    department_ids: list[int] = []
    raw_departments = item.get("department") or []
    if isinstance(raw_departments, list):
        for value in raw_departments:
            try:
                department_id = int(value)
            except (TypeError, ValueError):
                continue
            if department_id > 0 and department_id not in department_ids:
                department_ids.append(department_id)
    return {
        "userid": userid,
        "name": name,
        "email": email[:_EMAIL_LIMIT],
        "department_ids": department_ids,
    }


def _api_error(path: str, errcode: int) -> WecomDirectoryError:
    suffix = f"（errcode={errcode}）" if errcode >= 0 else ""
    scope_denied = errcode in _SCOPE_ERRCODES
    department_hidden = path == "/department/list" and errcode in _DEPARTMENT_ERRCODES
    if scope_denied or department_hidden:
        return WecomDirectoryError(
            f"企微应用无权读取通讯录{suffix}",
            status_code=409,
            code="WECOM_DIRECTORY_EMPTY_SCOPE",
            errcode=errcode,
        )
    if path == "/user/list" and errcode in _DEPARTMENT_ERRCODES:
        return WecomDirectoryError(
            f"部门不存在或不在可见范围内{suffix}",
            status_code=404,
            code="WECOM_DIRECTORY_DEPARTMENT_NOT_FOUND",
            errcode=errcode,
        )
    return WecomDirectoryError(
        f"企微通讯录请求失败{suffix}",
        status_code=502,
        code="WECOM_DIRECTORY_REQUEST_FAILED",
        errcode=errcode,
    )


def _errcode(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("errcode", -1))
    except (TypeError, ValueError):
        return -1
