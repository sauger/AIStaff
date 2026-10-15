from __future__ import annotations

from typing import Any

import httpx

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
    login_data: Any | None = None,
) -> dict[str, str]:
    kind = (template or "").strip().lower()
    if not str(token or "").strip() and cookie:
        if kind == "custom":
            headers = _custom_login_headers(
                user_id=user_id,
                custom_map=custom_map,
                cookie=cookie,
                token="",
                login_data=login_data,
            )
            headers.setdefault("Cookie", cookie)
            return headers
        headers = {"Cookie": cookie}
        if kind == "new_api" and user_id:
            headers["New-Api-User"] = user_id
        return headers
    if kind == "new_api":
        headers = {"Authorization": token}
        if user_id:
            headers["New-Api-User"] = user_id
        return headers
    if kind == "bearer":
        return {"Authorization": f"Bearer {token}"}
    if kind == "cookie":
        return {"Cookie": cookie or token}
    return _custom_login_headers(
        user_id=user_id,
        custom_map=custom_map,
        cookie=cookie,
        token=token,
        login_data=login_data,
    )


def _custom_login_headers(
    *,
    user_id: str | None,
    custom_map: dict[str, str],
    cookie: str | None,
    token: str,
    login_data: Any | None,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    values = {"token": token, "user_id": user_id or "", "cookie": cookie or token}
    for header_name, source in (custom_map or {}).items():
        value = values.get(source)
        if not value and login_data is not None:
            value = extract_json_path(login_data, source)
        if value:
            headers[str(header_name)] = value
    return headers


def cookie_header_from_login_response(response: Any) -> str | None:
    """Build a Cookie header value from httpx response cookies or Set-Cookie."""
    header = _cookie_header_from_jar(getattr(response, "cookies", None))
    if header:
        return header
    headers = getattr(response, "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("set-cookie")
    if not raw:
        return None
    text = str(raw)
    return text.split(";", 1)[0] if ";" in text else text


def _cookie_header_from_jar(cookies: Any) -> str | None:
    if not cookies:
        return None
    jar = getattr(cookies, "jar", None)
    if jar is not None:
        pairs: list[str] = []
        seen: set[str] = set()
        try:
            items = list(jar)
        except TypeError:
            items = []
        for cookie in items:
            name = str(getattr(cookie, "name", "") or "")
            value = getattr(cookie, "value", None)
            if not name or value is None or name in seen:
                continue
            seen.add(name)
            pairs.append(f"{name}={value}")
        if pairs:
            return "; ".join(pairs)
    try:
        names = list(cookies.keys())
    except (AttributeError, TypeError):
        return None
    pairs = []
    seen = set()
    for name in names:
        if name in seen:
            continue
        try:
            value = cookies[name]
        except (httpx.CookieConflict, KeyError):
            continue
        seen.add(str(name))
        pairs.append(f"{name}={value}")
    if not pairs:
        return None
    return "; ".join(pairs)


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
