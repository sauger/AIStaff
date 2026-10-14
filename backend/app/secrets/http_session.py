from __future__ import annotations

from typing import Any

UNAUTH_MESSAGE_MARKERS = ("未登录", "登录已过期", "token 无效", "unauthorized", "not login")
CAPTCHA_MARKERS = ("turnstile", "captcha", "2fa", "totp", "passkey", "验证码", "双因素")


def extract_json_path(data: Any, path: str) -> str | None:
    current = data
    for part in (item for item in path.split(".") if item):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    if current is None:
        return None
    return str(current)


def session_headers_from_login(
    *,
    template: str,
    token: str,
    user_id: str | None,
    custom_map: dict[str, str],
    cookie: str | None = None,
) -> dict[str, str]:
    kind = (template or "").strip().lower()
    if kind == "new_api":
        headers = {"Authorization": token}
        if user_id:
            headers["New-Api-User"] = user_id
        return headers
    if kind == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if kind == "cookie":
        return {"Cookie": cookie or token}
    headers: dict[str, str] = {}
    values = {"token": token, "user_id": user_id or "", "cookie": cookie or token}
    for header_name, source in (custom_map or {}).items():
        value = values.get(source) or extract_json_path(values, source)
        if value:
            headers[str(header_name)] = value
    return headers


def is_unauthenticated_response(status_code: int, payload: Any) -> bool:
    if status_code in {401, 403}:
        return True
    if not isinstance(payload, dict):
        return False
    if payload.get("success") is not False:
        return False
    message = str(payload.get("message") or payload.get("msg") or "").lower()
    return any(marker.lower() in message for marker in UNAUTH_MESSAGE_MARKERS)


_LOGIN_ERROR_TEXT_KEYS = ("message", "msg", "error", "detail", "error_message", "err_msg")


def login_error_indicates_captcha(payload: Any, text: str = "") -> bool:
    """Detect captcha/2FA hints from login error text only (never session tokens)."""
    parts: list[str] = []
    if isinstance(payload, dict):
        for key in _LOGIN_ERROR_TEXT_KEYS:
            value = payload.get(key)
            if value is not None:
                parts.append(str(value))
    elif text:
        parts.append(text)
    if not parts:
        return False
    blob = " ".join(parts).lower()
    return any(marker.lower() in blob for marker in CAPTCHA_MARKERS)
