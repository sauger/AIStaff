from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.published_deliverables import (
    find_published_deliverable,
    list_published_deliverables,
)
from app.db.models import HarnessTaskFrameRecord, Message


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> None:
    db.add(
        HarnessTaskFrameRecord(
            id="htask_cit",
            tenant_id="tenant_cit",
            session_id="session_cit",
            source_turn_id="turn_cit",
            task_id="task_cit",
            status="completed",
        )
    )
    db.add(
        Message(
            id="msg_cit",
            tenant_id="tenant_cit",
            session_id="session_cit",
            role="assistant",
            content="done",
            metadata_json={
                "harness_artifacts": [
                    {
                        "type": "workspace_file",
                        "task_frame_id": "task_cit",
                        "path": "report.docx",
                        "display_name": "report.docx",
                        "size": 10,
                        "sha256": "a" * 64,
                    }
                ],
                "knowledge_citations": [
                    {
                        "label": "[1]",
                        "title": "差旅报销流程",
                        "source_path": "财务.md / 差旅报销流程",
                        "section_path": "差旅报销流程",
                    }
                ],
            },
        )
    )
    db.commit()


def test_list_published_deliverables_stays_compact() -> None:
    with Session(_engine()) as db:
        _seed(db)
        rows = list_published_deliverables(
            db, tenant_id="tenant_cit", session_id="session_cit"
        )
    assert "knowledge_citations" not in rows[0]


def test_find_published_deliverable_preserves_citations() -> None:
    with Session(_engine()) as db:
        _seed(db)
        row = find_published_deliverable(
            db,
            tenant_id="tenant_cit",
            session_id="session_cit",
            task_frame_id="task_cit",
            path="report.docx",
        )
    assert row is not None
    assert row["knowledge_citations"][0]["source_path"].startswith("财务.md")


def test_read_published_deliverable_returns_citations(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    from app.core import harness_capability_invoker as invoker_module
    from app.core.harness_capability_invoker import HarnessCapabilityInvoker

    monkeypatch.setattr(
        invoker_module,
        "find_published_deliverable",
        lambda *args, **kwargs: {
            "task_frame_id": "task_cit",
            "path": "report.docx",
            "display_name": "report.docx",
            "size": 10,
            "sha256": "a" * 64,
            "knowledge_citations": [{"title": "差旅报销流程"}],
        },
    )
    monkeypatch.setattr(
        invoker_module,
        "open_harness_artifact",
        lambda *args, **kwargs: SimpleNamespace(
            size=10, sha256=lambda: "a" * 64, close=lambda: None
        ),
    )
    monkeypatch.setattr(invoker_module, "_workspace_root", lambda *args, **kwargs: tmp_path)

    class FakeExecutor:
        def execute(self, context, call):
            return SimpleNamespace(success=True, data={"content": "ok"})

    invoker = object.__new__(HarnessCapabilityInvoker)
    invoker.db = None
    invoker.tenant_id = "tenant_cit"
    invoker.session = SimpleNamespace(id="session_cit")
    invoker.run_id = "run_cit"
    invoker._file_context = SimpleNamespace(
        limits=SimpleNamespace(max_result_bytes=10000),
        sandbox_enabled=False,
        sandbox_network_mode="deny",
        sandbox_allowed_domains=(),
    )
    invoker._file_executor = FakeExecutor()

    result = invoker._read_published_deliverable(
        {"task_frame_id": "task_cit", "path": "report.docx"}
    )
    assert result["success"] is True
    assert result["citations"] == [{"title": "差旅报销流程"}]
