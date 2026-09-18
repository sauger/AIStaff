from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlparse

from app.secrets.errors import SecretError

CAPTCHA_PATTERN = re.compile(
    r"验证码|双因素|二次验证|人机验证|滑动验证|两步验证|"
    r"captcha|two[- ]?factor|2fa|\botp\b|\btotp\b|authenticator|"
    r"verification code|one[- ]time (?:pass|code)",
    re.IGNORECASE,
)

_LOGIN_FORM_HINT = re.compile(r"type=['\"]password['\"]|name=['\"]password['\"]", re.IGNORECASE)


@dataclass(frozen=True)
class LoginAttempt:
    url: str
    username_selector: str
    password_selector: str
    submit_selector: str
    username: str | None
    password: str | None
    storage_state: str | None
    secret_type: str


@dataclass(frozen=True)
class LoginExecution:
    success: bool
    captcha_or_2fa: bool
    storage_state: str | None
    message: str


class LoginExecutor(Protocol):
    def execute(self, attempt: LoginAttempt) -> LoginExecution:
        ...


@dataclass
class FakeLoginExecutor:
    """Test double. Never talks to the network and never echoes credentials."""

    mode: str = "success"
    snapshot_payload: str = '{"cookies":[{"name":"sid","value":"fresh-session"}]}'
    attempts: list[LoginAttempt] = field(default_factory=list)

    def execute(self, attempt: LoginAttempt) -> LoginExecution:
        self.attempts.append(attempt)
        if attempt.secret_type == "session_snapshot" or attempt.storage_state:
            if self.mode == "fail":
                return LoginExecution(False, False, None, "会话快照已失效，请更新后再试。")
            state = attempt.storage_state or self.snapshot_payload
            return LoginExecution(True, False, state, "已使用会话快照登录。")
        if self.mode == "captcha":
            return LoginExecution(False, True, None, "需要人工处理验证码")
        if self.mode == "fail":
            return LoginExecution(False, False, None, "登录失败。")
        return LoginExecution(True, False, self.snapshot_payload, "登录成功。")


class HttpFormLoginExecutor:
    """Best-effort HTTP form login. Never solves captcha or 2FA."""

    def execute(self, attempt: LoginAttempt) -> LoginExecution:
        _assert_http_url(attempt.url)
        try:
            import httpx
            from bs4 import BeautifulSoup
        except ImportError as exc:  # pragma: no cover - runtime optional
            raise SecretError(
                "LOGIN_RUNTIME_UNAVAILABLE",
                "登录运行时未就绪。",
            ) from exc

        timeout = httpx.Timeout(20.0)
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            if attempt.storage_state:
                _apply_storage_state(client, attempt.storage_state)
            try:
                response = client.get(attempt.url)
            except httpx.HTTPError as exc:
                raise SecretError("LOGIN_FAILED", _safe_exc(exc, attempt)) from exc
            html = response.text or ""
            if _looks_like_captcha(html):
                return LoginExecution(False, True, None, "需要人工处理验证码")
            if attempt.storage_state:
                if _still_on_login_form(html):
                    return LoginExecution(False, False, None, "会话快照已失效，请更新后再试。")
                return LoginExecution(
                    True,
                    False,
                    _dump_storage_state(client),
                    "已使用会话快照登录。",
                )
            if not attempt.username or not attempt.password:
                raise SecretError("LOGIN_SECRET_INVALID", "密码密钥缺少用户名或密码，无法填写登录表单。")
            soup = BeautifulSoup(html, "html.parser")
            form = _find_form(soup, attempt)
            if form is None:
                return LoginExecution(
                    False,
                    False,
                    None,
                    "登录页无法用当前运行时完成（页面可能需要浏览器）。请补一条会话快照密钥。",
                )
            payload = _form_payload(form, attempt)
            action = form.get("action") or attempt.url
            method = str(form.get("method") or "post").lower()
            target = urljoin(str(response.url), str(action))
            try:
                submitted = client.request(method, target, data=payload)
            except httpx.HTTPError as exc:
                raise SecretError("LOGIN_FAILED", _safe_exc(exc, attempt)) from exc
            result_html = submitted.text or ""
            if _looks_like_captcha(result_html):
                return LoginExecution(False, True, None, "需要人工处理验证码")
            if _still_on_login_form(result_html) and submitted.url == response.url:
                return LoginExecution(False, False, None, "登录失败，请检查密钥或登录说明。")
            return LoginExecution(
                True,
                False,
                _dump_storage_state(client),
                "登录成功。",
            )


_executor: LoginExecutor | None = None


def get_login_executor() -> LoginExecutor:
    return _executor if _executor is not None else HttpFormLoginExecutor()


def set_login_executor(executor: LoginExecutor | None) -> None:
    global _executor
    _executor = executor


def _assert_http_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SecretError("LOGIN_URL_INVALID", "登录地址必须是 http 或 https URL。")


def _looks_like_captcha(html: str) -> bool:
    return bool(CAPTCHA_PATTERN.search(html or ""))


def _still_on_login_form(html: str) -> bool:
    return bool(_LOGIN_FORM_HINT.search(html or ""))


def _find_form(soup, attempt: LoginAttempt):
    if attempt.password_selector:
        field = soup.select_one(attempt.password_selector)
        if field is not None:
            form = field.find_parent("form")
            if form is not None:
                return form
    forms = soup.find_all("form")
    for form in forms:
        if form.find("input", {"type": "password"}) is not None:
            return form
    return forms[0] if forms else None


def _form_payload(form, attempt: LoginAttempt) -> dict[str, str]:
    payload: dict[str, str] = {}
    for element in form.find_all(["input", "select", "textarea"]):
        name = element.get("name")
        if not name:
            continue
        payload[str(name)] = str(element.get("value") or "")
    username_el = form.select_one(attempt.username_selector) if attempt.username_selector else None
    password_el = form.select_one(attempt.password_selector) if attempt.password_selector else None
    if username_el is not None and username_el.get("name"):
        payload[str(username_el["name"])] = attempt.username or ""
    elif attempt.username:
        payload.setdefault("username", attempt.username)
        payload.setdefault("user", attempt.username)
        payload.setdefault("email", attempt.username)
    if password_el is not None and password_el.get("name"):
        payload[str(password_el["name"])] = attempt.password or ""
    elif attempt.password:
        payload["password"] = attempt.password
    return payload


def _apply_storage_state(client, raw: str) -> None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SecretError("LOGIN_SECRET_INVALID", "会话快照格式无效。") from exc
    cookies = data.get("cookies") if isinstance(data, dict) else None
    if not isinstance(cookies, list):
        raise SecretError("LOGIN_SECRET_INVALID", "会话快照格式无效。")
    for item in cookies:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        client.cookies.set(
            str(item.get("name")),
            str(item.get("value") or ""),
            domain=str(item.get("domain") or "") or None,
            path=str(item.get("path") or "/") or "/",
        )


def _dump_storage_state(client) -> str:
    cookies = []
    jar = getattr(client.cookies, "jar", None)
    items = list(jar) if jar is not None else []
    if not items:
        for name, value in client.cookies.items():
            cookies.append({"name": name, "value": value, "domain": "", "path": "/"})
    else:
        for cookie in items:
            cookies.append(
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": getattr(cookie, "domain", "") or "",
                    "path": getattr(cookie, "path", "/") or "/",
                    "secure": bool(getattr(cookie, "secure", False)),
                    "httpOnly": bool(getattr(cookie, "rest", {}).get("HttpOnly", False)),
                }
            )
    return json.dumps({"cookies": cookies, "origins": []}, ensure_ascii=True)


def _safe_exc(exc: BaseException, attempt: LoginAttempt) -> str:
    text = str(exc)
    for secret in (attempt.password, attempt.username, attempt.storage_state):
        if secret and secret in text:
            text = text.replace(secret, "******")
    return text or "登录失败。"
