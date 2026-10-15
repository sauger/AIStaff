from types import SimpleNamespace

import httpx

from app.secrets.http_session import (
    cookie_header_from_login_response,
    extract_json_path,
    is_unauthenticated_response,
    session_headers_from_login,
)


def test_extract_nested_token() -> None:
    data = {"success": True, "data": {"token": "abc", "id": 7}}
    assert extract_json_path(data, "data.token") == "abc"
    assert extract_json_path(data, "data.id") == "7"


def test_new_api_template_headers() -> None:
    headers = session_headers_from_login(
        template="new_api",
        token="abc",
        user_id="7",
        custom_map={},
    )
    assert headers == {"Authorization": "abc", "New-Api-User": "7"}


def test_new_api_cookie_session_without_token() -> None:
    headers = session_headers_from_login(
        template="new_api",
        token="",
        user_id="7",
        custom_map={},
        cookie="session=sess-1",
    )
    assert headers == {"Cookie": "session=sess-1", "New-Api-User": "7"}


def test_bearer_template_headers() -> None:
    headers = session_headers_from_login(
        template="bearer",
        token="abc",
        user_id=None,
        custom_map={},
    )
    assert headers == {"Authorization": "Bearer abc"}


def test_unauthenticated_http_401() -> None:
    assert is_unauthenticated_response(401, {"success": False}) is True


def test_unauthenticated_json_success_false() -> None:
    assert is_unauthenticated_response(
        200, {"success": False, "message": "未登录或登录已过期"}
    ) is True


def test_business_400_is_not_unauthenticated() -> None:
    assert is_unauthenticated_response(
        400, {"success": False, "message": "period_ym 无效"}
    ) is False


def test_duplicate_cookie_names_keep_first_value() -> None:
    class _Cookie:
        def __init__(self, name: str, value: str) -> None:
            self.name = name
            self.value = value

    class _Jar:
        def __iter__(self):
            return iter([
                _Cookie("session", "one"),
                _Cookie("session", "two"),
                _Cookie("sid", "x"),
            ])

    class _Cookies:
        jar = _Jar()

        def keys(self):
            return ["session", "sid"]

        def __getitem__(self, name: str) -> str:
            raise httpx.CookieConflict(name)

    response = SimpleNamespace(cookies=_Cookies(), headers={})

    assert cookie_header_from_login_response(response) == "session=one; sid=x"


def test_cookie_conflict_falls_back_to_set_cookie_header() -> None:
    class _Cookies:
        jar = None

        def keys(self):
            return ["session"]

        def __getitem__(self, name: str) -> str:
            raise httpx.CookieConflict(name)

    response = SimpleNamespace(
        cookies=_Cookies(),
        headers={"set-cookie": "session=sess-1; Path=/; HttpOnly"},
    )

    assert cookie_header_from_login_response(response) == "session=sess-1"


def test_cookie_template_uses_set_cookie() -> None:
    headers = session_headers_from_login(
        template="cookie",
        token="ignored",
        user_id=None,
        custom_map={},
        cookie="session=abc123",
    )
    assert headers == {"Cookie": "session=abc123"}


def test_custom_template_reads_json_path_from_login_body() -> None:
    login_body = {"success": True, "data": {"token": "t1", "role": "admin"}}
    headers = session_headers_from_login(
        template="custom",
        token="t1",
        user_id="7",
        custom_map={"X-Role": "data.role"},
        login_data=login_body,
    )
    assert headers == {"X-Role": "admin"}
