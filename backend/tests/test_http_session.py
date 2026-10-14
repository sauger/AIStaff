from app.secrets.http_session import (
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
