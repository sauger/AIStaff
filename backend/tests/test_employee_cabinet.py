from __future__ import annotations

import base64
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.agents.schema import AgentProfileCreateRequest
from app.api.agents import create_agent
from app.cabinet.api import (
    create_folder,
    download_file,
    list_cabinet,
    remove_entry,
    upload_file,
)
from app.cabinet.errors import CabinetError
from app.cabinet.harness import invoke_cabinet_tool
from app.cabinet.schema import CabinetFolderCreateRequest, CabinetUploadRequest
from app.cabinet.service import (
    CHAT_INBOX_FOLDER,
    MAX_FILE_BYTES,
    find_by_name,
    ingest_chat_attachments,
    list_folder,
    produce_from_template,
    read_file_bytes,
    used_bytes,
)
from app.core.capability_manifest import (
    RESERVED_HARNESS_CAPABILITY_NAMES,
    CapabilityManifestBuilder,
)
from app.db.models import (
    AgentProfile,
    EmployeeCabinetEntry,
    KnowledgeDocument,
    Tenant,
    User,
)
from app.session.attachment_store import stage_chat_attachment
from app.session.attachments import parse_chat_attachment


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session) -> tuple[User, User, User, AgentProfile, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    owner = User(
        id="user_owner",
        tenant_id="tenant_demo",
        username="owner",
        display_name="Owner",
        password_hash="x",
    )
    other = User(
        id="user_other",
        tenant_id="tenant_demo",
        username="other",
        display_name="Other",
        password_hash="x",
    )
    admin = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        display_name="Admin",
        role="admin",
        password_hash="x",
    )
    agent_a = AgentProfile(
        id="agent_a",
        tenant_id="tenant_demo",
        name="员工A",
        is_overall=False,
        metadata_json={"owner_user_id": owner.id},
    )
    agent_b = AgentProfile(
        id="agent_b",
        tenant_id="tenant_demo",
        name="员工B",
        is_overall=False,
        metadata_json={"owner_user_id": other.id},
    )
    db.add(owner)
    db.add(other)
    db.add(admin)
    db.add(agent_a)
    db.add(agent_b)
    db.commit()
    return owner, other, admin, agent_a, agent_b


def _upload(
    db: Session,
    user: User,
    agent_id: str,
    filename: str,
    data: bytes,
    *,
    path: str = "",
    overwrite: bool = False,
):
    return upload_file(
        CabinetUploadRequest(
            tenant_id="tenant_demo",
            path=path,
            filename=filename,
            content_base64=base64.b64encode(data).decode("ascii"),
            overwrite=overwrite,
        ),
        agent_id=agent_id,
        db=db,
        current_user=user,
    )


def _xlsx_bytes(text: str = "报价模板") -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"></Types>',
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            (
                '<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f"<si><t>{text}</t></si></sst>"
            ),
        )
    return buffer.getvalue()


def _xlsx_shared_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return archive.read("xl/sharedStrings.xml").decode("utf-8")


@pytest.fixture
def cabinet_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _engine()
    with Session(engine) as db:
        users = _seed(db)
        yield db, users


def test_owner_upload_download_roundtrip(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    payload = b"template-bytes"
    uploaded = _upload(db, owner, agent_a.id, "报价模板.xlsx", payload)
    listing = list_cabinet(
        tenant_id="tenant_demo", agent_id=agent_a.id, path="", db=db, current_user=owner
    )
    assert [item.name for item in listing.entries] == ["报价模板.xlsx"]
    downloaded = download_file(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        path=uploaded.path,
        db=db,
        current_user=owner,
    )
    assert downloaded.body == payload


def test_cabinet_upload_does_not_create_knowledge_document(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"abc")
    docs = db.exec(select(KnowledgeDocument)).all()
    assert docs == []


def test_chat_attachment_lands_in_inbox(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    attachment = parse_chat_attachment("客户名单.csv", "text/csv", b"name,city\n", extract_text=False)
    staged = stage_chat_attachment(
        attachment, b"name,city\n", tenant_id="tenant_demo", user_id=owner.id
    )
    saved = ingest_chat_attachments(
        db,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        user_id=owner.id,
        attachments=[staged],
    )
    assert saved[0].path == f"{CHAT_INBOX_FOLDER}/客户名单.csv"
    listing = list_folder(db, "tenant_demo", agent_a.id, CHAT_INBOX_FOLDER, can_write=True)
    assert [item.name for item in listing.entries] == ["客户名单.csv"]


def test_produce_from_template_saves_new_file_and_keeps_original(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    original = _xlsx_bytes("报价模板")
    _upload(db, owner, agent_a.id, "报价模板.xlsx", original)
    produced = produce_from_template(
        db,
        "tenant_demo",
        agent_a.id,
        template="报价模板.xlsx",
        destination_path="报价-客户A.xlsx",
        replacements={"报价模板": "客户A报价"},
    )
    _, original_bytes = read_file_bytes(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    _, produced_bytes = read_file_bytes(db, "tenant_demo", agent_a.id, produced.path)
    assert original_bytes == original
    assert "客户A报价" in _xlsx_shared_text(produced_bytes)
    assert produced.path != "报价模板.xlsx"


def test_produce_without_overwrite_rejects_same_path(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", _xlsx_bytes())
    with pytest.raises(CabinetError) as error:
        produce_from_template(
            db,
            "tenant_demo",
            agent_a.id,
            template="报价模板.xlsx",
            destination_path="报价模板.xlsx",
            overwrite=False,
        )
    assert error.value.code == "CABINET_TEMPLATE_PRESERVED"


def test_explicit_overwrite_replaces_template(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", _xlsx_bytes("旧模板"))
    produce_from_template(
        db,
        "tenant_demo",
        agent_a.id,
        template="报价模板.xlsx",
        destination_path="报价模板.xlsx",
        overwrite=True,
        replacements={"旧模板": "新模板"},
    )
    _, data = read_file_bytes(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert "新模板" in _xlsx_shared_text(data)


def test_unprocessable_template_fails_without_new_file(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "印章.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
    with pytest.raises(CabinetError) as error:
        produce_from_template(
            db,
            "tenant_demo",
            agent_a.id,
            template="印章.png",
            destination_path="新印章.png",
            replacements={"x": "y"},
        )
    assert error.value.code == "CABINET_TEMPLATE_UNPROCESSABLE"
    listing = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    assert [item.name for item in listing.entries] == ["印章.png"]


def test_upload_accepts_unrestricted_formats(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    for filename, payload in (
        ("说明.pdf", b"%PDF-1.4 test"),
        ("合同.docx", b"PK\x03\x04docx"),
        ("报价.xlsx", _xlsx_bytes()),
        ("图.png", b"\x89PNG\r\n\x1a\n"),
        ("无扩展名", b"\x00\x01\x02binary"),
    ):
        uploaded = _upload(db, owner, agent_a.id, filename, payload)
        assert uploaded.name == filename


def test_console_upload_same_path_requires_overwrite(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"v1")
    with pytest.raises(Exception) as error:
        _upload(db, owner, agent_a.id, "报价模板.xlsx", b"v2")
    assert error.value.detail["code"] == "CABINET_EXISTS"
    _, original = read_file_bytes(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert original == b"v1"
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"v2", overwrite=True)
    _, replaced = read_file_bytes(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert replaced == b"v2"


def test_upload_rejects_file_over_limit(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    with pytest.raises(Exception) as error:
        _upload(db, owner, agent_a.id, "过大.bin", b"a" * (MAX_FILE_BYTES + 1))
    assert "超过单文件上限" in str(error.value.detail["message"])
    assert error.value.detail["size_bytes"] == MAX_FILE_BYTES + 1
    assert error.value.detail["max_file_bytes"] == MAX_FILE_BYTES


def test_admin_can_browse_and_download_others_but_cannot_write(cabinet_db) -> None:
    db, (owner, _other, admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"secret")
    listing = list_cabinet(
        tenant_id="tenant_demo", agent_id=agent_a.id, path="", db=db, current_user=admin
    )
    assert listing.can_write is False
    downloaded = download_file(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        path="报价模板.xlsx",
        db=db,
        current_user=admin,
    )
    assert downloaded.body == b"secret"
    with pytest.raises(Exception) as upload_error:
        _upload(db, admin, agent_a.id, "注入.txt", b"nope")
    assert upload_error.value.status_code == 403
    with pytest.raises(Exception) as delete_error:
        remove_entry(
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            path="报价模板.xlsx",
            db=db,
            current_user=admin,
        )
    assert delete_error.value.status_code == 403
    with pytest.raises(Exception) as folder_error:
        create_folder(
            CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="新文件夹"),
            agent_id=agent_a.id,
            db=db,
            current_user=admin,
        )
    assert folder_error.value.status_code == 403


def test_admin_can_write_own_employee_cabinet(cabinet_db) -> None:
    db, (_owner, _other, admin, _agent_a, _agent_b) = cabinet_db
    owned = AgentProfile(
        id="agent_admin",
        tenant_id="tenant_demo",
        name="管理员自己的员工",
        metadata_json={"owner_user_id": admin.id},
    )
    db.add(owned)
    db.commit()
    uploaded = _upload(db, admin, owned.id, "笔记.txt", b"mine")
    assert uploaded.name == "笔记.txt"
    remove_entry(
        tenant_id="tenant_demo",
        agent_id=owned.id,
        path="笔记.txt",
        db=db,
        current_user=admin,
    )


def test_non_admin_cannot_list_other_employee_cabinet(cabinet_db) -> None:
    db, (owner, other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "秘密.xlsx", b"hidden-name")
    with pytest.raises(Exception) as error:
        list_cabinet(
            tenant_id="tenant_demo", agent_id=agent_a.id, path="", db=db, current_user=other
        )
    assert error.value.status_code == 403
    assert "秘密.xlsx" not in str(error.value.detail)


def test_runtime_cannot_read_another_employee_cabinet(cabinet_db, tmp_path: Path) -> None:
    db, (owner, _other, _admin, agent_a, agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"private-a")
    invoker = SimpleNamespace(
        db=db,
        tenant_id="tenant_demo",
        agent_id=agent_b.id,
        workspace_root=tmp_path / "workspace-b",
    )
    invoker.workspace_root.mkdir()
    result = invoke_cabinet_tool(invoker, "cabinet_read", {"path": "报价模板.xlsx"})
    assert result["success"] is False
    assert result["error"]["code"] == "CABINET_FILE_NOT_FOUND"
    assert "private-a" not in str(result)


def test_copy_agent_does_not_copy_cabinet(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"keep-on-a")
    copied = create_agent(
        AgentProfileCreateRequest(
            tenant_id="tenant_demo",
            name="复制员工",
            copy_from_agent_id=agent_a.id,
            source_mode="copy",
        ),
        db=db,
        current_user=owner,
    )
    source_listing = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    target_listing = list_folder(db, "tenant_demo", copied.id, "", can_write=True)
    assert [item.name for item in source_listing.entries] == ["报价模板.xlsx"]
    assert target_listing.entries == []


def test_copying_skill_bindings_does_not_copy_cabinet(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, agent_b) = cabinet_db
    from app.api.agents import _copy_agent_scope_from_source
    from app.db.models import AgentResourceBinding, Skill

    skill = Skill(
        tenant_id="tenant_demo",
        skill_id="skill_quote",
        name="报价SOP",
        version="1.0.0",
        content_json={},
        status="published",
    )
    db.add(skill)
    db.flush()
    db.add(
        AgentResourceBinding(
            tenant_id="tenant_demo",
            agent_id=agent_a.id,
            resource_type="skill",
            resource_id=skill.id,
            status="active",
        )
    )
    db.commit()
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"template")
    _copy_agent_scope_from_source(db, "tenant_demo", agent_a, agent_b)
    listing = list_folder(db, "tenant_demo", agent_b.id, "", can_write=True)
    assert listing.entries == []
    source = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    assert [item.name for item in source.entries] == ["报价模板.xlsx"]


def test_explicit_save_only_stores_selected_workspace_file(cabinet_db, tmp_path: Path) -> None:
    db, (_owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    workspace = tmp_path / "run"
    workspace.mkdir()
    (workspace / "one.txt").write_bytes(b"keep")
    (workspace / "two.txt").write_bytes(b"skip-2")
    (workspace / "three.txt").write_bytes(b"skip-3")
    invoker = SimpleNamespace(
        db=db,
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        workspace_root=workspace,
    )
    saved = invoke_cabinet_tool(
        invoker,
        "cabinet_save",
        {"workspace_path": "one.txt", "cabinet_path": "交付/one.txt"},
    )
    assert saved["success"] is True
    listing = list_folder(db, "tenant_demo", agent_a.id, "交付", can_write=True)
    assert [item.name for item in listing.entries] == ["one.txt"]
    assert used_bytes(db, "tenant_demo", agent_a.id) == len(b"keep")


def test_artifacts_are_not_auto_ingested(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    before = db.exec(select(EmployeeCabinetEntry)).all()
    assert before == []
    _upload(db, owner, agent_a.id, "已有.txt", b"stay")
    after = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    assert [item.name for item in after.entries] == ["已有.txt"]


def test_ambiguous_filename_requires_clarification(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    create_folder(
        CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="模板"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    create_folder(
        CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="备份"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"one", path="模板")
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"two", path="备份")
    result = find_by_name(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert result.needs_clarification is True
    assert len(result.matches) == 2


def test_filename_query_does_not_prefer_root_path_silently(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    create_folder(
        CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="模板"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"root")
    _upload(db, owner, agent_a.id, "报价模板.xlsx", b"nested", path="模板")
    result = find_by_name(db, "tenant_demo", agent_a.id, "报价模板.xlsx")
    assert result.needs_clarification is True
    assert {item.path for item in result.matches} == {"报价模板.xlsx", "模板/报价模板.xlsx"}
    exact = find_by_name(db, "tenant_demo", agent_a.id, "模板/报价模板.xlsx")
    assert exact.needs_clarification is False
    assert exact.matches[0].path == "模板/报价模板.xlsx"


def test_delete_folder_does_not_treat_underscore_as_wildcard(cabinet_db) -> None:
    db, (owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    create_folder(
        CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="a_b"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    create_folder(
        CabinetFolderCreateRequest(tenant_id="tenant_demo", path="", name="axb"),
        agent_id=agent_a.id,
        db=db,
        current_user=owner,
    )
    _upload(db, owner, agent_a.id, "one.txt", b"keep-me", path="a_b")
    _upload(db, owner, agent_a.id, "two.txt", b"also-keep", path="axb")
    remove_entry(
        tenant_id="tenant_demo",
        agent_id=agent_a.id,
        path="a_b",
        db=db,
        current_user=owner,
    )
    listing = list_folder(db, "tenant_demo", agent_a.id, "", can_write=True)
    assert [item.name for item in listing.entries] == ["axb"]
    nested = list_folder(db, "tenant_demo", agent_a.id, "axb", can_write=True)
    assert [item.name for item in nested.entries] == ["two.txt"]


def test_manifest_includes_cabinet_tools(cabinet_db) -> None:
    db, (_owner, _other, _admin, agent_a, _agent_b) = cabinet_db
    manifest = CapabilityManifestBuilder(db).build("tenant_demo", agent_a.id, None, None)
    names = {item.name for item in manifest.available}
    assert {"cabinet_list", "cabinet_find", "cabinet_read", "cabinet_save", "cabinet_produce_from_template"} <= names
    assert "cabinet_save" in RESERVED_HARNESS_CAPABILITY_NAMES
