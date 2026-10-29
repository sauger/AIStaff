from __future__ import annotations

from pathlib import Path

import pytest

from app.honcho.sidecar_env import (
    HonchoSidecarApplyError,
    apply_sidecar_env_file,
    build_honcho_model_env,
)


def test_build_deriver_env_uses_openai_transport_and_the_same_chat_model() -> None:
    updates = build_honcho_model_env(
        deriver_model="deepseek-chat",
        deriver_base_url="https://api.deepseek.com",
        deriver_api_key="sk-deriver",
        embedding_model=None,
        embedding_base_url="",
        embedding_api_key="",
        embedding_dimensions=None,
    )

    assert updates["LLM_OPENAI_API_KEY"] == "sk-deriver"
    assert updates["DERIVER_MODEL_CONFIG__TRANSPORT"] == "openai"
    assert updates["DERIVER_MODEL_CONFIG__MODEL"] == "deepseek-chat"
    assert updates["DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL"] == "https://api.deepseek.com"
    for level in ("minimal", "low", "medium", "high", "max"):
        prefix = f"DIALECTIC_LEVELS__{level}__MODEL_CONFIG"
        assert updates[f"{prefix}__TRANSPORT"] == "openai"
        assert updates[f"{prefix}__MODEL"] == "deepseek-chat"
        assert updates[f"{prefix}__OVERRIDES__BASE_URL"] == "https://api.deepseek.com"
    assert updates["SUMMARY_MODEL_CONFIG__TRANSPORT"] == "openai"
    assert updates["SUMMARY_MODEL_CONFIG__MODEL"] == "deepseek-chat"
    assert updates["SUMMARY_MODEL_CONFIG__OVERRIDES__BASE_URL"] == "https://api.deepseek.com"
    for name in ("DREAM_DEDUCTION", "DREAM_INDUCTION"):
        assert updates[f"{name}_MODEL_CONFIG__TRANSPORT"] == "openai"
        assert updates[f"{name}_MODEL_CONFIG__MODEL"] == "deepseek-chat"
        assert updates[f"{name}_MODEL_CONFIG__OVERRIDES__BASE_URL"] == "https://api.deepseek.com"
    assert "EMBEDDING_MODEL_CONFIG__MODEL" not in updates
    assert "EMBEDDING_VECTOR_DIMENSIONS" not in updates


def test_build_embedding_env_keeps_a_separate_key_and_default_dimensions() -> None:
    updates = build_honcho_model_env(
        deriver_model=None,
        deriver_base_url="",
        deriver_api_key="should-not-write-without-deriver-model",
        embedding_model="text-embedding-v4",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_api_key="sk-embed",
        embedding_dimensions=None,
    )

    assert updates["EMBEDDING_MODEL_CONFIG__TRANSPORT"] == "openai"
    assert updates["EMBEDDING_MODEL_CONFIG__MODEL"] == "text-embedding-v4"
    assert (
        updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL"]
        == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    assert updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY"] == "sk-embed"
    assert updates["EMBEDDING_VECTOR_DIMENSIONS"] == "1536"
    assert "LLM_OPENAI_API_KEY" not in updates


def test_blank_embedding_key_does_not_schedule_a_key_rewrite() -> None:
    updates = build_honcho_model_env(
        deriver_model="deepseek-chat",
        deriver_base_url="",
        deriver_api_key="",
        embedding_model="text-embedding-v4",
        embedding_base_url="",
        embedding_api_key="",
        embedding_dimensions=1024,
    )

    assert "LLM_OPENAI_API_KEY" not in updates
    assert "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY" not in updates
    assert updates["DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL"] is None
    assert updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL"] is None
    assert updates["EMBEDDING_VECTOR_DIMENSIONS"] == "1024"


def test_unset_models_cannot_build_an_apply_payload() -> None:
    with pytest.raises(HonchoSidecarApplyError, match="对话抽取模型或向量嵌入模型"):
        build_honcho_model_env(
            deriver_model=None,
            deriver_base_url="",
            deriver_api_key="sk-ignored",
            embedding_model=None,
            embedding_base_url="",
            embedding_api_key="sk-ignored",
            embedding_dimensions=1536,
        )


def test_clearing_a_model_deletes_managed_keys_but_not_api_keys() -> None:
    updates = build_honcho_model_env(
        deriver_model="",
        deriver_base_url="",
        deriver_api_key="sk-deriver",
        embedding_model="",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        embedding_api_key="sk-embed",
        embedding_dimensions=1536,
    )

    assert updates["DERIVER_MODEL_CONFIG__MODEL"] is None
    assert updates["DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL"] is None
    assert updates["DIALECTIC_LEVELS__low__MODEL_CONFIG__MODEL"] is None
    assert updates["SUMMARY_MODEL_CONFIG__MODEL"] is None
    assert updates["DREAM_DEDUCTION_MODEL_CONFIG__OVERRIDES__BASE_URL"] is None
    assert updates["DREAM_INDUCTION_MODEL_CONFIG__MODEL"] is None
    assert updates["EMBEDDING_MODEL_CONFIG__MODEL"] is None
    assert updates["EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL"] is None
    assert updates["EMBEDDING_VECTOR_DIMENSIONS"] is None
    assert "LLM_OPENAI_API_KEY" not in updates
    assert "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY" not in updates


def test_clearing_models_removes_those_lines_and_keeps_secrets(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            [
                "LLM_OPENAI_API_KEY=keep-chat",
                "DERIVER_MODEL_CONFIG__MODEL=deepseek-chat",
                "DERIVER_MODEL_CONFIG__TRANSPORT=openai",
                "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://api.deepseek.com",
                "DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL=deepseek-chat",
                "SUMMARY_MODEL_CONFIG__MODEL=deepseek-chat",
                "DREAM_INDUCTION_MODEL_CONFIG__MODEL=deepseek-chat",
                "EMBEDDING_MODEL_CONFIG__MODEL=text-embedding-v4",
                "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://example.test/v1",
                "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY=keep-embed",
                "EMBEDDING_VECTOR_DIMENSIONS=1536",
                "UNRELATED=1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    updates = build_honcho_model_env(
        deriver_model="",
        deriver_base_url="",
        deriver_api_key="",
        embedding_model="text-embedding-v4",
        embedding_base_url="",
        embedding_api_key="",
        embedding_dimensions=1536,
    )
    apply_sidecar_env_file(str(path), updates)
    text = path.read_text(encoding="utf-8")

    assert "LLM_OPENAI_API_KEY=keep-chat" in text
    assert "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY=keep-embed" in text
    assert "UNRELATED=1" in text
    assert "DERIVER_MODEL_CONFIG__MODEL" not in text
    assert "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL" not in text
    assert "DIALECTIC_LEVELS__minimal__MODEL_CONFIG__MODEL" not in text
    assert "SUMMARY_MODEL_CONFIG__MODEL" not in text
    assert "DREAM_INDUCTION_MODEL_CONFIG__MODEL" not in text
    assert "EMBEDDING_MODEL_CONFIG__MODEL=text-embedding-v4" in text
    assert "EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL" not in text


def test_apply_updates_managed_keys_and_preserves_the_rest(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "DB_CONNECTION_URI=postgresql+psycopg://postgres:secret@localhost/postgres\n"
        "# LLM_OPENAI_API_KEY=commented\n"
        "export LLM_OPENAI_API_KEY=old-key\n"
        "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://old.example/v1\n"
        "UNRELATED=1\n",
        encoding="utf-8",
    )

    apply_sidecar_env_file(
        str(path),
        {
            "LLM_OPENAI_API_KEY": "sk-new",
            "DERIVER_MODEL_CONFIG__MODEL": "deepseek-chat",
            "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL": None,
            "EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY": 'sk-"quoted" #not-comment',
        },
    )

    text = path.read_text(encoding="utf-8")
    assert "postgresql+psycopg://postgres:secret@localhost/postgres" in text
    assert "# LLM_OPENAI_API_KEY=commented" in text
    assert "UNRELATED=1" in text
    assert "old-key" not in text
    assert "export LLM_OPENAI_API_KEY" not in text
    assert "LLM_OPENAI_API_KEY=sk-new" in text
    assert "DERIVER_MODEL_CONFIG__MODEL=deepseek-chat" in text
    assert "DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL" not in text
    quoted = [
        line
        for line in text.splitlines()
        if line.startswith("EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY=")
    ]
    assert len(quoted) == 1
    assert quoted[0].startswith('EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY="')
    assert "#not-comment" in quoted[0]
    assert (path.stat().st_mode & 0o777) == 0o600


def test_apply_rejects_blank_relative_and_missing_parent(tmp_path: Path) -> None:
    with pytest.raises(HonchoSidecarApplyError, match="HONCHO_SIDECAR_ENV_FILE"):
        apply_sidecar_env_file("  ", {"LLM_OPENAI_API_KEY": "sk"})
    with pytest.raises(HonchoSidecarApplyError, match="绝对路径"):
        apply_sidecar_env_file("honcho.env", {"LLM_OPENAI_API_KEY": "sk"})
    missing = tmp_path / "missing" / ".env"
    with pytest.raises(HonchoSidecarApplyError, match="目录不存在"):
        apply_sidecar_env_file(str(missing), {"LLM_OPENAI_API_KEY": "sk"})


def test_apply_rejects_newlines_without_rewriting_the_file(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n", encoding="utf-8")

    with pytest.raises(HonchoSidecarApplyError, match="换行"):
        apply_sidecar_env_file(str(path), {"LLM_OPENAI_API_KEY": "sk\ninjected=1"})

    assert path.read_text(encoding="utf-8") == "KEEP=1\n"


def test_write_failure_does_not_echo_the_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    path.write_text("KEEP=1\n", encoding="utf-8")

    def fail_write(target: Path, content: str) -> None:
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("app.honcho.sidecar_env._atomic_write", fail_write)
    with pytest.raises(HonchoSidecarApplyError, match="无法写入") as error:
        apply_sidecar_env_file(str(path), {"LLM_OPENAI_API_KEY": "sk-secret"})

    assert "sk-secret" not in str(error.value)
    assert path.read_text(encoding="utf-8") == "KEEP=1\n"


def test_unwritable_directory_leaves_the_file_unchanged(tmp_path: Path) -> None:
    parent = tmp_path / "locked"
    parent.mkdir()
    path = parent / ".env"
    path.write_text("KEEP=1\n", encoding="utf-8")
    parent.chmod(0o500)
    try:
        with pytest.raises(HonchoSidecarApplyError, match="不可写"):
            apply_sidecar_env_file(str(path), {"LLM_OPENAI_API_KEY": "sk-new"})
    finally:
        parent.chmod(0o700)

    assert path.read_text(encoding="utf-8") == "KEEP=1\n"
    assert "sk-new" not in path.read_text(encoding="utf-8")
