from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.dashboard.share import build_share_url, parse_share_token, share_token


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_token_is_stable_and_parses_back() -> None:
    token = share_token("dashview_abc")
    assert token.startswith("dashview_abc.")
    assert share_token("dashview_abc") == token
    assert parse_share_token(token) == "dashview_abc"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "dashview_abc",
        "dashview_abc.",
        ".abc",
        "dashview_abc.deadbeef",
        "dashview_abc.abc.def",
    ],
)
def test_malformed_tokens_are_rejected(token: str) -> None:
    assert parse_share_token(token) is None


def test_one_changed_character_is_rejected() -> None:
    token = share_token("dashview_abc")
    last = "A" if token[-1] != "A" else "B"
    assert parse_share_token(token[:-1] + last) is None


def test_changing_app_secret_invalidates_old_links(monkeypatch: pytest.MonkeyPatch) -> None:
    token = share_token("dashview_abc")
    monkeypatch.setattr(get_settings(), "app_secret", "a-different-secret")
    assert parse_share_token(token) is None


def test_build_share_url_prefers_public_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_base_url", "https://dash.example.com")
    url, scope = build_share_url("dashview_abc")
    assert scope == "public"
    assert url is not None
    assert url.startswith("https://dash.example.com/s/dashview_abc.")


def test_build_share_url_falls_back_to_request_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    request = SimpleNamespace(base_url="http://127.0.0.1:8765/")
    url, scope = build_share_url("dashview_abc", request)  # type: ignore[arg-type]
    assert scope == "local"
    assert url is not None
    assert url.startswith("http://127.0.0.1:8765/s/dashview_abc.")


def test_build_share_url_without_base_or_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_base_url", "")
    url, scope = build_share_url("dashview_abc")
    assert url is None
    assert scope == "local"
