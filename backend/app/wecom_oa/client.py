"""企微 OA HTTP 客户端：gettoken / applyevent / getapprovaldetail。"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.channels.adapters.wecom import WECOM_API_BASE, WECOM_TOKEN_REFRESH_SKEW_SECONDS
from app.wecom_oa.credentials import WecomOaCredentialError, load_plaintext_secrets

logger = logging.getLogger(__name__)


class WecomOaApiError(RuntimeError):
    """企微 OA API 业务/传输错误。"""

    def __init__(
        self,
        message: str,
        *,
        errcode: int | None = None,
        errmsg: str | None = None,
        code: str = "WECOM_OA_API_ERROR",
    ):
        super().__init__(message)
        self.errcode = errcode
        self.errmsg = errmsg
        self.code = code


# 可见范围 / 无权限等常见错误码映射
_ERRCODE_MAP: dict[int, str] = {
    60011: "WECOM_OA_VISIBLE_SCOPE",
    301055: "WECOM_OA_NO_PERMISSION",
    301025: "WECOM_OA_TEMPLATE_NOT_FOUND",
    40014: "WECOM_OA_INVALID_ACCESS_TOKEN",
    42001: "WECOM_OA_ACCESS_TOKEN_EXPIRED",
}


def map_errcode(errcode: int) -> str:
    return _ERRCODE_MAP.get(int(errcode), "WECOM_OA_API_ERROR")


class WecomOaTokenProvider:
    """独立于 bot ChannelBinding 的 OA access_token 缓存。"""

    def __init__(self, *, client_factory: Callable[[], httpx.Client] | None = None):
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=15.0))
        self._cache: dict[tuple[str, str], tuple[str, float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(credential: Any) -> tuple[str, str]:
        return credential.id, credential.corp_id

    def get(self, credential: Any, *, force_refresh: bool = False) -> str:
        key = self._key(credential)
        with self._lock:
            cached = self._cache.get(key)
            if cached and not force_refresh and cached[1] > time.monotonic():
                return cached[0]
        secrets = load_plaintext_secrets(credential)
        corp_id = secrets["corp_id"]
        secret = secrets["secret"]
        try:
            with self._client_factory() as client:
                response = client.get(
                    f"{WECOM_API_BASE}/gettoken",
                    params={"corpid": corp_id, "corpsecret": secret},
                )
                data = response.json()
        except (httpx.HTTPError, ValueError, WecomOaCredentialError) as exc:
            raise WecomOaApiError("企微 OA token 请求失败", code="WECOM_OA_TOKEN_FAILED") from exc
        errcode = int(data.get("errcode", -1))
        if response.status_code >= 400 or errcode != 0:
            raise WecomOaApiError(
                f"企微 OA token 请求失败: errcode={data.get('errcode')} msg={data.get('errmsg')}",
                errcode=errcode,
                errmsg=str(data.get("errmsg") or ""),
                code=map_errcode(errcode),
            )
        token = str(data.get("access_token") or "").strip()
        expires_in = int(data.get("expires_in") or 0)
        if not token or expires_in <= 0:
            raise WecomOaApiError(
                "企微 OA token 响应缺少必要字段", code="WECOM_OA_TOKEN_INVALID"
            )
        with self._lock:
            self._cache[key] = (
                token,
                time.monotonic() + max(1, expires_in - WECOM_TOKEN_REFRESH_SKEW_SECONDS),
            )
        return token

    def invalidate(self, credential: Any) -> None:
        with self._lock:
            self._cache.pop(self._key(credential), None)


class WecomOaClient:
    """封装 applyevent / getapprovaldetail；token 过期自动刷新一次。"""

    def __init__(
        self,
        credential: Any,
        *,
        token_provider: WecomOaTokenProvider | None = None,
        client_factory: Callable[[], httpx.Client] | None = None,
    ):
        self.credential = credential
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=20.0))
        self._tokens = token_provider or WecomOaTokenProvider(
            client_factory=self._client_factory
        )

    def apply_event(
        self,
        *,
        creator_userid: str,
        template_id: str,
        apply_data: dict[str, Any],
        use_template_approver: int = 1,
        summary_list: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        creator_userid = str(creator_userid or "").strip()
        template_id = str(template_id or "").strip()
        if not creator_userid or not template_id:
            raise WecomOaApiError(
                "applyevent 缺少 creator_userid 或 template_id",
                code="WECOM_OA_INVALID_ARGS",
            )
        body: dict[str, Any] = {
            "creator_userid": creator_userid,
            "template_id": template_id,
            "use_template_approver": int(use_template_approver),
            "apply_data": apply_data,
        }
        if summary_list:
            body["summary_list"] = summary_list
        data = self._post_with_token_retry("/oa/applyevent", body)
        sp_no = str(data.get("sp_no") or "").strip()
        if not sp_no:
            raise WecomOaApiError(
                "applyevent 响应缺少 sp_no",
                errcode=int(data.get("errcode") or 0) or None,
                errmsg=str(data.get("errmsg") or ""),
                code="WECOM_OA_NO_SP_NO",
            )
        return data

    def get_approval_detail(self, sp_no: str) -> dict[str, Any]:
        sp_no = str(sp_no or "").strip()
        if not sp_no:
            raise WecomOaApiError(
                "getapprovaldetail 缺少 sp_no", code="WECOM_OA_INVALID_ARGS"
            )
        return self._post_with_token_retry(
            "/oa/getapprovaldetail", {"sp_no": sp_no}
        )

    def get_template_detail(self, template_id: str) -> dict[str, Any]:
        template_id = str(template_id or "").strip()
        if not template_id:
            raise WecomOaApiError(
                "gettemplatedetail 缺少 template_id", code="WECOM_OA_INVALID_ARGS"
            )
        return self._post_with_token_retry(
            "/oa/gettemplatedetail", {"template_id": template_id}
        )

    def list_template_catalog(self) -> dict[str, Any]:
        """尝试读取模板目录。公开文档未保证该路径，调用方需处理不存在的情况。"""
        return self._post_with_token_retry("/oa/gettemplatelist", {})

    def list_approval_info(
        self,
        *,
        starttime: int,
        endtime: int,
        cursor: str = "",
        size: int = 100,
    ) -> dict[str, Any]:
        return self._post_with_token_retry(
            "/oa/getapprovalinfo",
            {
                "starttime": int(starttime),
                "endtime": int(endtime),
                "new_cursor": cursor,
                "size": int(size),
            },
        )

    def _post_with_token_retry(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        token = self._tokens.get(self.credential)
        try:
            return self._post(path, body, token)
        except WecomOaApiError as exc:
            if exc.errcode in {40014, 42001}:
                self._tokens.invalidate(self.credential)
                token = self._tokens.get(self.credential, force_refresh=True)
                return self._post(path, body, token)
            raise

    def _post(self, path: str, body: dict[str, Any], token: str) -> dict[str, Any]:
        try:
            with self._client_factory() as client:
                response = client.post(
                    f"{WECOM_API_BASE}{path}",
                    params={"access_token": token},
                    json=body,
                )
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WecomOaApiError(
                f"企微 OA 请求失败: {path}", code="WECOM_OA_HTTP_FAILED"
            ) from exc
        errcode = int(data.get("errcode", -1))
        if response.status_code >= 400 or errcode != 0:
            raise WecomOaApiError(
                f"企微 OA 调用失败 {path}: errcode={errcode} msg={data.get('errmsg')}",
                errcode=errcode,
                errmsg=str(data.get("errmsg") or ""),
                code=map_errcode(errcode),
            )
        return data
