from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app import version
from app.api import app_updates


def _feed(*entries: tuple[str, str]) -> bytes:
    body = "".join(
        (
            "<entry>"
            f"<updated>{published_at}</updated>"
            f'<link rel="alternate" href="https://github.com/OpenBMB/StaffDeck/releases/tag/{tag}" />'
            f"<title>{tag}</title>"
            "</entry>"
        )
        for tag, published_at in entries
    )
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{body}</feed>'.encode()


def _response(content: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=content,
        request=httpx.Request("GET", app_updates.RELEASES_FEED_URL),
    )


@pytest.fixture(autouse=True)
def clear_update_cache(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "_cached_result", None)


def test_version_comparison_handles_stable_prerelease_and_build_metadata() -> None:
    assert app_updates._is_newer("v0.2.0-beta.10", "0.2.0-beta.3") is True
    assert app_updates._is_newer("0.2.0", "0.2.0-beta.10") is True
    assert app_updates._is_newer("0.2.0-beta.3", "0.2.0") is False
    assert app_updates._is_newer("0.2", "0.2.0") is False
    assert app_updates._is_newer("1.0.0+build.2", "1.0.0+build.1") is False
    assert app_updates._is_newer("not-a-version", "0.2.0") is False


def test_stable_build_ignores_prereleases_when_selecting_latest() -> None:
    release = app_updates._parse_release_feed(
        _feed(
            ("v0.3.0-beta.2", "2026-08-05T12:00:00Z"),
            ("v0.2.1", "2026-08-04T12:00:00Z"),
        ),
        "0.2.0",
    )

    assert release is not None
    assert release.version == "0.2.1"


def test_supported_stable_line_ignores_legacy_prerelease_tags() -> None:
    release = app_updates._parse_release_feed(
        _feed(
            ("v0.12-beta.2", "2026-07-31T06:13:44Z"),
            ("v0.2.1", "2026-08-05T12:00:00Z"),
        ),
        "0.2.0",
    )

    assert release is not None
    assert release.version == "0.2.1"
    assert app_updates._is_newer(release.version, "0.2.0") is True


def test_prerelease_build_can_advance_to_newer_prerelease_or_stable() -> None:
    release = app_updates._parse_release_feed(
        _feed(
            ("v0.3.0-beta.2", "2026-08-05T12:00:00Z"),
            ("v0.2.1", "2026-08-04T12:00:00Z"),
        ),
        "0.3.0-beta.1",
    )

    assert release is not None
    assert release.version == "0.3.0-beta.2"


def test_fetch_version_uses_validated_github_release_url(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.2.0")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(("v0.2.1", "2026-08-05T12:00:00Z"))
        ),
    )

    result = app_updates._fetch_version()

    assert result.current_version == "0.2.0"
    assert result.latest_version == "0.2.1"
    assert result.update_available is True
    assert result.release_url.endswith("/releases/tag/v0.2.1")
    assert result.check_succeeded is True


def test_source_deployment_skips_network_check_by_default(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.2.0")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: False)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: pytest.fail("disabled checks must not access GitHub"),
    )

    result = app_updates._fetch_version()

    assert result.check_enabled is False
    assert result.check_succeeded is False
    assert result.update_available is False


def test_fetch_version_fails_silently_and_failure_is_cached(monkeypatch) -> None:
    calls = 0

    def fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(app_updates, "app_version", lambda: "0.2.0")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(app_updates.httpx, "get", fail)

    first = app_updates.get_app_version()
    second = app_updates.get_app_version()

    assert first.check_succeeded is False
    assert first.update_available is False
    assert second == first
    assert calls == 1


def test_release_tag_rejects_untrusted_alternate_link() -> None:
    content = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>tag:github.com,2008:Repository/1/v0.2.1</id>
        <link rel="alternate" href="https://example.com/releases/tag/v9.9.9" />
        <title>Untrusted link</title>
      </entry>
    </feed>"""

    release = app_updates._parse_release_feed(content, "0.2.0")

    assert release is not None
    assert release.version == "0.2.1"
    assert release.url == app_updates.RELEASES_PAGE_URL


def test_shipped_build_stamp_matches_itself_and_is_not_an_update(monkeypatch) -> None:
    """0.1.15-<shortsha> is the shipped identity, not a lower semver than upstream tags."""
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-769ecd2")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v1.0.0-preview", "2026-10-21T09:39:55Z"),
                ("v0.5.6", "2026-09-11T10:15:02Z"),
                ("v0.1.15-769ecd2", "2026-10-29T03:42:58Z"),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.current_version == "0.1.15-769ecd2"
    assert result.latest_version == "0.1.15-769ecd2"
    assert result.update_available is False
    assert result.check_succeeded is True


def test_shipped_build_stamp_ignores_unrelated_release_line(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-769ecd2")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v1.0.0-preview", "2026-10-21T09:39:55Z"),
                ("v0.5.6", "2026-09-11T10:15:02Z"),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.current_version == "0.1.15-769ecd2"
    assert result.latest_version == "0.1.15-769ecd2"
    assert result.update_available is False
    assert result.check_succeeded is True


def test_newer_build_stamp_is_an_update(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-769ecd2")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v0.5.6", "2026-09-11T10:15:02Z"),
                ("v0.1.16-abcdef0", "2026-11-01T00:00:00Z"),
                ("v0.1.15-769ecd2", "2026-10-29T03:42:58Z"),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.latest_version == "0.1.16-abcdef0"
    assert result.update_available is True
    assert result.current_version == "0.1.15-769ecd2"


def test_later_published_build_of_the_same_release_is_newer(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-aaaaaaa")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v0.1.15-aaaaaaa", "2026-08-01T00:00:00Z"),
                ("v0.1.15-769ecd2", "2026-10-29T03:42:58Z"),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.latest_version == "0.1.15-769ecd2"
    assert result.update_available is True
    assert app_updates._is_newer("0.1.15-769ecd2", "0.1.15-aaaaaaa") is False
    assert app_updates._is_newer("0.1.15-769ecd2", "0.1.15-769ECD2") is False
    assert app_updates._is_newer("v0.5.6", "0.1.15-769ecd2") is False
    assert app_updates._is_newer("v1.0.0-preview", "0.1.15-769ecd2") is False


def test_missing_running_stamp_is_not_downgraded_to_another_sha(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-769ecd2")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(("v0.1.15-aaaaaaa", "2026-11-02T00:00:00Z"))
        ),
    )

    result = app_updates._fetch_version()

    assert result.latest_version == "0.1.15-769ecd2"
    assert result.update_available is False
    assert result.check_succeeded is True


def test_same_publish_time_does_not_order_build_ids(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-769ecd2")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    published = "2026-10-29T03:42:58Z"
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v0.1.15-aaaaaaa", published),
                ("v0.1.15-769ecd2", published),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.update_available is False
    assert result.latest_version == "0.1.15-769ecd2"


def test_build_publish_times_compare_as_instants(monkeypatch) -> None:
    monkeypatch.setattr(app_updates, "app_version", lambda: "0.1.15-aaaaaaa")
    monkeypatch.setattr(app_updates, "update_check_enabled", lambda: True)
    monkeypatch.setattr(
        app_updates.httpx,
        "get",
        lambda *args, **kwargs: _response(
            _feed(
                ("v0.1.15-aaaaaaa", "2026-10-29T04:00:00+02:00"),
                ("v0.1.15-769ecd2", "2026-10-29T03:00:00Z"),
            )
        ),
    )

    result = app_updates._fetch_version()

    assert result.latest_version == "0.1.15-769ecd2"
    assert result.update_available is True


def test_plain_release_ignores_git_build_stamps() -> None:
    release = app_updates._parse_release_feed(
        _feed(
            ("v0.9.0-abcdef0", "2026-11-01T00:00:00Z"),
            ("v0.5.6", "2026-09-11T10:15:02Z"),
        ),
        "0.5.6",
    )

    assert release is not None
    assert release.version == "0.5.6"
    assert app_updates._is_newer(release.version, "0.5.6") is False

    prerelease = app_updates._parse_release_feed(
        _feed(
            ("v0.4.0-abcdef0", "2026-11-01T00:00:00Z"),
            ("v0.3.0-beta.2", "2026-08-05T12:00:00Z"),
        ),
        "0.3.0-beta.1",
    )

    assert prerelease is not None
    assert prerelease.version == "0.3.0-beta.2"


def test_app_version_priority_and_macos_resources(monkeypatch, tmp_path: Path) -> None:
    app_root = tmp_path / "StaffDeck.app"
    executable = app_root / "Contents" / "MacOS" / "staffdeck"
    resource = app_root / "Contents" / "Resources" / "staffdeck-version.txt"
    executable.parent.mkdir(parents=True)
    resource.parent.mkdir(parents=True)
    resource.write_text("0.2.1\n", encoding="utf-8")

    monkeypatch.delattr(version.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(version.sys, "frozen", True, raising=False)
    monkeypatch.setattr(version.sys, "platform", "darwin")
    monkeypatch.setattr(version.sys, "executable", str(executable))
    monkeypatch.delenv("STAFFDECK_VERSION", raising=False)

    assert version.app_version() == "0.2.1"
    monkeypatch.setenv("STAFFDECK_VERSION", "0.3.0")
    assert version.app_version() == "0.3.0"


@pytest.mark.parametrize(
    ("configured", "frozen", "expected"),
    [("true", False, True), ("false", True, False), ("", True, True), ("", False, False)],
)
def test_update_check_enablement(monkeypatch, configured: str, frozen: bool, expected: bool) -> None:
    if configured:
        monkeypatch.setenv("STAFFDECK_UPDATE_CHECK", configured)
    else:
        monkeypatch.delenv("STAFFDECK_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(version.sys, "frozen", frozen, raising=False)
    assert version.update_check_enabled() is expected
