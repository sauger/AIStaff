from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.chat import (
    ArtifactLineEditRequest,
    edit_harness_artifact_line,
    read_harness_artifact_content,
)
from app.core.artifact_line_editor import (
    ArtifactLineEditError,
    normalize_model_replacement,
    replace_source_line,
)
from app.core.harness_session_cleanup import harness_task_workspace_path
from app.db.models import (
    AgentEvent,
    ChatSession,
    HarnessTaskFrameRecord,
    Message,
    Tenant,
    User,
)
from app.harness import publish_harness_artifacts
from app.llm import LLMError


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


TENANT_ID = "tenant_demo"
SESSION_ID = "session_demo"
TASK_FRAME_ID = "task_demo"
ARTIFACT_PATH = "reports/结论说明.md"
ORIGINAL_TEXT = "# 标题\n\n这一行需要被改得更短一些，因为它实在是太长了。\n\n- 第一项\n"

SENTINEL_MODEL = object()


def _seed_session(db: Session, *, session_status: str = "active") -> User:
    db.add(Tenant(id=TENANT_ID, name="Demo"))
    user = User(
        id="user_owner",
        tenant_id=TENANT_ID,
        username="owner",
        password_hash="test",
    )
    db.add(user)
    db.add(
        ChatSession(
            id=SESSION_ID,
            tenant_id=TENANT_ID,
            user_id=user.id,
            status=session_status,
        )
    )
    db.add(
        HarnessTaskFrameRecord(
            id="htask_demo",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            source_turn_id="turn_demo",
            task_id=TASK_FRAME_ID,
        )
    )
    return user


def _publish_markdown_artifact(db: Session, workspace: Path) -> None:
    file_path = workspace / "reports" / "结论说明.md"
    file_path.parent.mkdir(parents=True)
    file_path.write_text(ORIGINAL_TEXT, encoding="utf-8", newline="")
    published = publish_harness_artifacts(
        workspace, TASK_FRAME_ID, [{"path": ARTIFACT_PATH}], operation="publish_artifact"
    )
    artifact = dict(published[0])
    artifact["display_name"] = "结论说明.md"
    db.add(
        Message(
            id="msg_assistant",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role="assistant",
            content="文件已生成。",
            metadata_json={"harness_artifacts": [artifact]},
        )
    )
    db.commit()


DOCX_PARAGRAPHS = ["周报标题", "第二段写得有点啰嗦，需要被改得更短。", "第三段收尾。"]
DOCX_ARTIFACT_PATH = "reports/周报.docx"


def _publish_docx_artifact(db: Session, workspace: Path) -> None:
    from docx import Document

    file_path = workspace / "reports" / "周报.docx"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    for paragraph in DOCX_PARAGRAPHS:
        document.add_paragraph(paragraph)
    document.save(str(file_path))
    published = publish_harness_artifacts(
        workspace, TASK_FRAME_ID, [{"path": DOCX_ARTIFACT_PATH}], operation="publish_artifact"
    )
    artifact = dict(published[0])
    artifact["display_name"] = "周报.docx"
    db.add(
        Message(
            id="msg_assistant_docx",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role="assistant",
            content="Word 已生成。",
            metadata_json={"harness_artifacts": [artifact]},
        )
    )
    db.commit()


def _docx_paragraph_texts(file_path: Path) -> list[str]:
    from docx import Document

    return [paragraph.text for paragraph in Document(str(file_path)).paragraphs]


def _session_messages(db: Session) -> list[Message]:
    return list(
        db.exec(select(Message).where(Message.session_id == SESSION_ID)).all()
    )


def _artifact_file(workspace: Path) -> Path:
    return workspace / "reports" / "结论说明.md"


def test_content_endpoint_returns_line_numbered_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)

        payload = read_harness_artifact_content(
            SESSION_ID,
            TASK_FRAME_ID,
            tenant_id=TENANT_ID,
            path=ARTIFACT_PATH,
            current_user=user,
            db=db,
        )

        assert payload["content"] == ORIGINAL_TEXT
        assert payload["display_name"] == "结论说明.md"
        assert payload["editable"] is True


def test_content_endpoint_rejects_non_editable_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        sheet = workspace / "reports" / "结论说明.xlsx"
        sheet.parent.mkdir(parents=True)
        sheet.write_bytes(b"PK\x03\x04")
        published = publish_harness_artifacts(
            workspace, TASK_FRAME_ID, [{"path": "reports/结论说明.xlsx"}]
        )
        db.add(
            Message(
                tenant_id=TENANT_ID,
                session_id=SESSION_ID,
                role="assistant",
                content="生成表格",
                metadata_json={"harness_artifacts": list(published)},
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as error:
            read_harness_artifact_content(
                SESSION_ID,
                TASK_FRAME_ID,
                tenant_id=TENANT_ID,
                path="reports/结论说明.xlsx",
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 400


def test_line_edit_rewrites_only_the_requested_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    rewrite_calls: list[dict[str, object]] = []

    def fake_rewrite(model_config, *, display_name, line_number, original_line, instruction):
        rewrite_calls.append(
            {
                "display_name": display_name,
                "line_number": line_number,
                "original_line": original_line,
                "instruction": instruction,
            }
        )
        return "改短后的第二行。"

    monkeypatch.setattr("app.api.chat.rewrite_artifact_line", fake_rewrite)
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)

    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)

        response = edit_harness_artifact_line(
            SESSION_ID,
            TASK_FRAME_ID,
            ArtifactLineEditRequest(
                tenant_id=TENANT_ID,
                path=ARTIFACT_PATH,
                line_number=3,
                original_line="这一行需要被改得更短一些，因为它实在是太长了。",
                instruction="把这一行改得更短",
            ),
            current_user=user,
            db=db,
        )

        assert rewrite_calls == [
            {
                "display_name": "结论说明.md",
                "line_number": 3,
                "original_line": "这一行需要被改得更短一些，因为它实在是太长了。",
                "instruction": "把这一行改得更短",
            }
        ]
        assert response["new_lines"] == ["改短后的第二行。"]
        expected_text = ORIGINAL_TEXT.replace(
            "这一行需要被改得更短一些，因为它实在是太长了。", "改短后的第二行。"
        )
        assert response["content"] == expected_text
        assert _artifact_file(workspace).read_text(encoding="utf-8") == expected_text

        assistant = db.get(Message, response["message"].id)
        assert assistant is not None
        assert assistant.role == "assistant"
        assert "结论说明.md" in assistant.content
        assert "第 3 行" in assistant.content
        # 结果消息保持简短，不把整篇新文档贴回气泡。
        assert "改短后的第二行。" not in assistant.content
        edit_metadata = assistant.metadata_json["artifact_line_edit"]
        assert edit_metadata["line_number"] == 3
        assert edit_metadata["instruction"] == "把这一行改得更短"

        event = db.exec(
            select(AgentEvent).where(
                AgentEvent.tenant_id == TENANT_ID,
                AgentEvent.session_id == SESSION_ID,
                AgentEvent.event_type == "assistant_message_created",
            )
        ).first()
        assert event is not None
        assert event.payload_json["message_id"] == assistant.id

        # 发布元数据同步了新摘要，下载校验继续通过。
        published_row = db.get(Message, "msg_assistant")
        refreshed = published_row.metadata_json["harness_artifacts"][0]
        assert refreshed["sha256"] == response["sha256"]
        assert refreshed["size"] == response["size"]
        assert refreshed["size"] == len(expected_text.encode("utf-8"))


def test_line_edit_rejects_stale_original_line_without_touching_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)
        message_count_before = len(_session_messages(db))

        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID,
                TASK_FRAME_ID,
                ArtifactLineEditRequest(
                    tenant_id=TENANT_ID,
                    path=ARTIFACT_PATH,
                    line_number=3,
                    original_line="旧版本里的一行",
                    instruction="改短",
                ),
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "LINE_STALE"

        assert _artifact_file(workspace).read_text(encoding="utf-8") == ORIGINAL_TEXT
        assert len(_session_messages(db)) == message_count_before


def test_line_edit_rejects_out_of_range_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)

        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID,
                TASK_FRAME_ID,
                ArtifactLineEditRequest(
                    tenant_id=TENANT_ID,
                    path=ARTIFACT_PATH,
                    line_number=99,
                    original_line="anything",
                    instruction="改短",
                ),
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 400
        assert error.value.detail["code"] == "LINE_OUT_OF_RANGE"


def test_line_edit_blocks_busy_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db, session_status="running")
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)

        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID,
                TASK_FRAME_ID,
                ArtifactLineEditRequest(
                    tenant_id=TENANT_ID,
                    path=ARTIFACT_PATH,
                    line_number=1,
                    original_line="# 标题",
                    instruction="改标题",
                ),
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "SESSION_BUSY"


def test_line_edit_requires_session_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        _seed_session(db)
        intruder = User(
            id="user_intruder",
            tenant_id=TENANT_ID,
            username="intruder",
            password_hash="test",
        )
        db.add(intruder)
        db.commit()
        request = ArtifactLineEditRequest(
            tenant_id=TENANT_ID,
            path=ARTIFACT_PATH,
            line_number=1,
            original_line="# 标题",
            instruction="改标题",
        )
        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID, TASK_FRAME_ID, request, current_user=intruder, db=db
            )
        assert error.value.status_code == 404


def test_line_edit_keeps_file_untouched_when_model_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))

    def failing_rewrite(*args, **kwargs):
        raise LLMError("MODEL_CONNECTION_FAILED", status_code=502)

    monkeypatch.setattr("app.api.chat.rewrite_artifact_line", failing_rewrite)
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_markdown_artifact(db, workspace)

        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID,
                TASK_FRAME_ID,
                ArtifactLineEditRequest(
                    tenant_id=TENANT_ID,
                    path=ARTIFACT_PATH,
                    line_number=3,
                    original_line="这一行需要被改得更短一些，因为它实在是太长了。",
                    instruction="把这一行改得更短",
                ),
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 502

        assert _artifact_file(workspace).read_text(encoding="utf-8") == ORIGINAL_TEXT
        assert len(_session_messages(db)) == 1  # 只有原发布消息，未追加结果


def test_replace_source_line_preserves_carriage_returns_and_neighbors() -> None:
    text = "alpha\r\nbeta\r\ngamma"
    assert replace_source_line(text, 2, "BETA") == "alpha\r\nBETA\r\ngamma"

    lf_text = "one\ntwo\nthree\n"
    assert replace_source_line(lf_text, 3, "THREE") == "one\ntwo\nTHREE\n"
    # 行号 4 是结尾的空行，替换后其余行保持逐字节一致
    assert replace_source_line(lf_text, 4, "tail") == "one\ntwo\nthree\ntail"


def test_normalize_model_replacement_strips_wrapping_fence() -> None:
    assert normalize_model_replacement("```\n新的这一行\n```") == "新的这一行"
    assert normalize_model_replacement("  直接输出  ") == "直接输出"
    assert normalize_model_replacement("多行\n结果") == "多行\n结果"
    with pytest.raises(ArtifactLineEditError):
        normalize_model_replacement("```")


def test_content_endpoint_returns_docx_paragraphs_as_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_docx_artifact(db, workspace)

        payload = read_harness_artifact_content(
            SESSION_ID,
            TASK_FRAME_ID,
            tenant_id=TENANT_ID,
            path=DOCX_ARTIFACT_PATH,
            current_user=user,
            db=db,
        )

        assert payload["content"] == "\n".join(DOCX_PARAGRAPHS)
        assert payload["kind"] == "docx"
        assert payload["display_name"] == "周报.docx"


def test_docx_line_edit_rewrites_only_the_requested_paragraph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    rewrite_calls: list[dict[str, object]] = []

    def fake_rewrite(model_config, *, display_name, line_number, original_line, instruction):
        rewrite_calls.append({"line_number": line_number, "original_line": original_line})
        return "改短的第二段。"

    monkeypatch.setattr("app.api.chat.rewrite_artifact_line", fake_rewrite)
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_docx_artifact(db, workspace)
        file_path = workspace / "reports" / "周报.docx"

        response = edit_harness_artifact_line(
            SESSION_ID,
            TASK_FRAME_ID,
            ArtifactLineEditRequest(
                tenant_id=TENANT_ID,
                path=DOCX_ARTIFACT_PATH,
                line_number=2,
                original_line=DOCX_PARAGRAPHS[1],
                instruction="把这一段改得更短",
            ),
            current_user=user,
            db=db,
        )

        assert rewrite_calls == [
            {"line_number": 2, "original_line": DOCX_PARAGRAPHS[1]}
        ]
        assert response["new_lines"] == ["改短的第二段。"]
        assert response["content"] == "\n".join(
            [DOCX_PARAGRAPHS[0], "改短的第二段。", DOCX_PARAGRAPHS[2]]
        )
        # 磁盘上的 Word 文档只换了第二段，其余段落原文一致。
        assert _docx_paragraph_texts(file_path) == [
            DOCX_PARAGRAPHS[0],
            "改短的第二段。",
            DOCX_PARAGRAPHS[2],
        ]
        assistant = db.get(Message, response["message"].id)
        assert assistant is not None
        assert "周报.docx" in assistant.content
        assert "第 2 行" in assistant.content

        published_row = db.get(Message, "msg_assistant_docx")
        refreshed = published_row.metadata_json["harness_artifacts"][0]
        assert refreshed["sha256"] == response["sha256"]
        assert refreshed["size"] == response["size"]


def test_docx_line_edit_supports_multi_line_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "app.api.chat.rewrite_artifact_line",
        lambda *args, **kwargs: "拆分后的第一点\n拆分后的第二点",
    )
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_docx_artifact(db, workspace)

        response = edit_harness_artifact_line(
            SESSION_ID,
            TASK_FRAME_ID,
            ArtifactLineEditRequest(
                tenant_id=TENANT_ID,
                path=DOCX_ARTIFACT_PATH,
                line_number=2,
                original_line=DOCX_PARAGRAPHS[1],
                instruction="拆成两段",
            ),
            current_user=user,
            db=db,
        )

        assert response["new_lines"] == ["拆分后的第一点", "拆分后的第二点"]
        texts = _docx_paragraph_texts(workspace / "reports" / "周报.docx")
        assert texts == [
            DOCX_PARAGRAPHS[0],
            "拆分后的第一点",
            "拆分后的第二点",
            DOCX_PARAGRAPHS[2],
        ]


def test_docx_line_edit_rejects_stale_paragraph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("app.api.chat.model_for_agent", lambda *args, **kwargs: SENTINEL_MODEL)
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        _publish_docx_artifact(db, workspace)

        with pytest.raises(HTTPException) as error:
            edit_harness_artifact_line(
                SESSION_ID,
                TASK_FRAME_ID,
                ArtifactLineEditRequest(
                    tenant_id=TENANT_ID,
                    path=DOCX_ARTIFACT_PATH,
                    line_number=2,
                    original_line="旧版本的第二段",
                    instruction="改短",
                ),
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "LINE_STALE"
        assert _docx_paragraph_texts(workspace / "reports" / "周报.docx") == DOCX_PARAGRAPHS


def test_content_endpoint_rejects_pdf_for_line_editing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_session(db)
        workspace = harness_task_workspace_path(
            tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=TASK_FRAME_ID
        )
        report = workspace / "reports" / "结论说明.pdf"
        report.parent.mkdir(parents=True)
        report.write_bytes(b"%PDF-1.4 minimal")
        published = publish_harness_artifacts(
            workspace, TASK_FRAME_ID, [{"path": "reports/结论说明.pdf"}]
        )
        db.add(
            Message(
                tenant_id=TENANT_ID,
                session_id=SESSION_ID,
                role="assistant",
                content="生成 PDF",
                metadata_json={"harness_artifacts": list(published)},
            )
        )
        db.commit()

        with pytest.raises(HTTPException) as error:
            read_harness_artifact_content(
                SESSION_ID,
                TASK_FRAME_ID,
                tenant_id=TENANT_ID,
                path="reports/结论说明.pdf",
                current_user=user,
                db=db,
            )
        assert error.value.status_code == 400
