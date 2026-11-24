"""Deployment profile must fail fast instead of silently degrading the SOP runtime."""

from __future__ import annotations

from app.config import Settings, runtime_profile_problems
from app.db.seed import _seed_password


def _settings(**overrides) -> Settings:
    base = {
        "app_secret": "x" * 32,
        "workflow_runtime_enabled": True,
        "allow_local_artifact_links": False,
    }
    base.update(overrides)
    return Settings(**base)


def test_dev_keeps_historical_defaults() -> None:
    assert runtime_profile_problems(_settings(deployment_profile="dev", workflow_runtime_enabled=False)) == []


def test_unknown_profile_is_rejected() -> None:
    problems = runtime_profile_problems(_settings(deployment_profile="staging"))
    assert len(problems) == 1
    assert "DEPLOYMENT_PROFILE" in problems[0]


def test_demo_requires_workflow_runtime() -> None:
    problems = runtime_profile_problems(
        _settings(deployment_profile="demo", workflow_runtime_enabled=False)
    )
    assert any("WORKFLOW_RUNTIME_ENABLED" in problem for problem in problems)


def test_prod_requires_a_real_secret() -> None:
    default_secret = runtime_profile_problems(
        _settings(deployment_profile="prod", app_secret="change-me-in-development")
    )
    assert any("APP_SECRET" in problem for problem in default_secret)
    short_secret = runtime_profile_problems(_settings(deployment_profile="prod", app_secret="short"))
    assert any("APP_SECRET" in problem for problem in short_secret)


def test_prod_requires_local_artifact_links_off() -> None:
    problems = runtime_profile_problems(
        _settings(deployment_profile="prod", allow_local_artifact_links=True)
    )
    assert any("ALLOW_LOCAL_ARTIFACT_LINKS" in problem for problem in problems)


def test_prod_is_clean_when_configured() -> None:
    assert runtime_profile_problems(_settings(deployment_profile="prod")) == []


def test_seed_password_prefers_configured_value() -> None:
    assert _seed_password(Settings(demo_admin_password="  pw  "), "demo_admin_password") == "pw"
    assert _seed_password(Settings(demo_admin_password=""), "demo_admin_password") == ""
    assert _seed_password(object(), "demo_admin_password") == ""
