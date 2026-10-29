from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Optional
from uuid import uuid4

from sqlalchemy import JSON, Column, Index, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


class Tenant(SQLModel, table=True):
    __tablename__ = "tenants"

    id: str = Field(primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "username", name="uq_user_tenant_username"),
        Index("ix_users_tenant_id_display_name", "tenant_id", "display_name"),
    )

    id: str = Field(default_factory=lambda: new_id("user"), primary_key=True)
    tenant_id: str = Field(index=True)
    username: str = Field(index=True)
    display_name: Optional[str] = None
    role: str = Field(default="member", index=True)
    # 账号来源:web=网页端创建;wechat 等=渠道懒建(用户管理列表默认隐藏)
    source: str = Field(default="web", index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserAvatar(SQLModel, table=True):
    """用户头像:小图以 data_url 直接存库(与聊天附件内联方式一致),

    独立小表避免 users 热表膨胀;create_all 建表,无需 ALTER。
    """

    __tablename__ = "user_avatars"

    user_id: str = Field(primary_key=True)
    data_url: str
    updated_at: datetime = Field(default_factory=utc_now)


class APIClient(SQLModel, table=True):
    """Server-to-server identity for the versioned public API."""

    __tablename__ = "api_clients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_api_client_tenant_name"),
    )

    id: str = Field(default_factory=lambda: new_id("apiclient"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    scopes_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class APICredential(SQLModel, table=True):
    """Hashed tenant or agent credential. Plaintext is returned exactly once."""

    __tablename__ = "api_credentials"
    __table_args__ = (
        UniqueConstraint("key_prefix", name="uq_api_credential_prefix"),
    )

    id: str = Field(default_factory=lambda: new_id("apicred"), primary_key=True)
    tenant_id: str = Field(index=True)
    client_id: str = Field(index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    name: str
    key_prefix: str = Field(index=True)
    key_digest: str
    scopes_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    last_used_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    revoked_at: Optional[datetime] = None


class APIIdempotencyRecord(SQLModel, table=True):
    __tablename__ = "api_idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "credential_id", "method", "path", "idempotency_key",
            name="uq_api_idempotency_request",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("apiidem"), primary_key=True)
    tenant_id: str = Field(index=True)
    credential_id: str = Field(index=True)
    method: str
    path: str
    idempotency_key: str = Field(index=True)
    request_hash: str
    status_code: int = 200
    response_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    resource_id: Optional[str] = Field(default=None, index=True)
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class APIJob(SQLModel, table=True):
    __tablename__ = "api_jobs"

    id: str = Field(default_factory=lambda: new_id("apijob"), primary_key=True)
    tenant_id: str = Field(index=True)
    credential_id: str = Field(index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    kind: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    stage: str = Field(default="queued", index=True)
    progress: float = 0.0
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    cancel_requested: bool = False
    retryable: bool = False
    execution_owner: Optional[str] = Field(default=None, index=True)
    execution_generation: int = 0
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class APIJobEvent(SQLModel, table=True):
    __tablename__ = "api_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_api_job_event_sequence"),
    )

    id: str = Field(default_factory=lambda: new_id("apievent"), primary_key=True)
    tenant_id: str = Field(index=True)
    job_id: str = Field(index=True)
    sequence: int = Field(index=True)
    event_type: str = Field(index=True)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    public: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class A2ATaskRun(SQLModel, table=True):
    """Durable state for outbound A2A calls and locally served A2A tasks."""

    __tablename__ = "a2a_task_runs"

    id: str = Field(default_factory=lambda: new_id("a2arun"), primary_key=True)
    direction: str = Field(default="client", index=True)
    tenant_id: str = Field(index=True)
    tool_id: Optional[str] = Field(default=None, index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    invocation_id: Optional[str] = Field(default=None, index=True)
    endpoint_url: str
    agent_card_url: Optional[str] = None
    protocol_binding: str = "JSONRPC"
    protocol_version: str = "1.0"
    remote_task_id: Optional[str] = Field(default=None, index=True)
    context_id: Optional[str] = Field(default=None, index=True)
    codex_session_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="submitted", index=True)
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    artifacts_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    agent_card_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    last_event_id: Optional[str] = Field(default=None, index=True)
    cancel_requested: bool = False
    recovery_attempts: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class A2ATaskEvent(SQLModel, table=True):
    __tablename__ = "a2a_task_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_a2a_task_event_sequence"),
    )

    id: str = Field(default_factory=lambda: new_id("a2aevt"), primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(index=True)
    sequence: int = Field(index=True)
    external_event_id: Optional[str] = Field(default=None, index=True)
    event_type: str = Field(index=True)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class ExternalBusinessTask(SQLModel, table=True):
    """Durable state for an HTTP tool whose business work finishes asynchronously."""

    __tablename__ = "external_business_tasks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "tool_id",
            "external_task_id",
            name="uq_external_business_task",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("exttask"), primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    task_frame_id: Optional[str] = Field(default=None, index=True)
    resume_step_id: Optional[str] = None
    invocation_id: Optional[str] = Field(default=None, index=True)
    tool_id: str = Field(index=True)
    external_task_id: Optional[str] = Field(default=None, index=True)
    idempotency_key: Optional[str] = Field(default=None, unique=True, index=True)
    status: str = Field(default="submitting", index=True)
    request_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    callback_token_hash: str
    status_url: Optional[str] = None
    status_config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    poll_interval_seconds: float = 5.0
    next_poll_at: Optional[datetime] = Field(default=None, index=True)
    poll_attempts: int = 0
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    accepted_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ExternalBusinessTaskEvent(SQLModel, table=True):
    __tablename__ = "external_business_task_events"
    __table_args__ = (
        UniqueConstraint("task_id", "event_id", name="uq_external_business_task_event"),
    )

    id: str = Field(default_factory=lambda: new_id("exttaskevt"), primary_key=True)
    tenant_id: str = Field(index=True)
    task_id: str = Field(index=True)
    event_id: str = Field(index=True)
    event_type: str = Field(index=True)
    data_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class WebhookEndpoint(SQLModel, table=True):
    __tablename__ = "webhook_endpoints"

    id: str = Field(default_factory=lambda: new_id("webhook"), primary_key=True)
    tenant_id: str = Field(index=True)
    client_id: str = Field(index=True)
    name: str
    url: str
    secret_encrypted: str
    events_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WebhookDelivery(SQLModel, table=True):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("endpoint_id", "event_id", name="uq_webhook_delivery_event"),
    )

    id: str = Field(default_factory=lambda: new_id("whdelivery"), primary_key=True)
    tenant_id: str = Field(index=True)
    endpoint_id: str = Field(index=True)
    event_id: str = Field(index=True)
    event_type: str = Field(index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="queued", index=True)
    delivery_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    attempt_count: int = 0
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    delivered_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class ExternalSessionBinding(SQLModel, table=True):
    __tablename__ = "external_session_bindings"
    __table_args__ = (
        UniqueConstraint(
            "credential_id", "external_session_id",
            name="uq_external_session_credential_id",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("extsession"), primary_key=True)
    tenant_id: str = Field(index=True)
    credential_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    external_session_id: str = Field(index=True)
    external_user_id: Optional[str] = Field(default=None, index=True)
    session_id: str = Field(index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class APISOPDraft(SQLModel, table=True):
    __tablename__ = "api_sop_drafts"

    id: str = Field(default_factory=lambda: new_id("sopdraft"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    base_version: Optional[str] = Field(default=None, index=True)
    draft_version: str = Field(index=True)
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="draft", index=True)
    source: str = Field(default="structured", index=True)
    warnings_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    validation_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    etag: str = Field(index=True)
    created_by_credential_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    published_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class APIAuditLog(SQLModel, table=True):
    __tablename__ = "api_audit_logs"

    id: str = Field(default_factory=lambda: new_id("apiaudit"), primary_key=True)
    tenant_id: Optional[str] = Field(default=None, index=True)
    credential_id: Optional[str] = Field(default=None, index=True)
    request_id: str = Field(index=True)
    method: str
    path: str
    resource_type: Optional[str] = Field(default=None, index=True)
    resource_id: Optional[str] = Field(default=None, index=True)
    status_code: int = Field(index=True)
    duration_ms: float
    created_at: datetime = Field(default_factory=utc_now)


class Skill(SQLModel, table=True):
    __tablename__ = "skills"
    __table_args__ = (UniqueConstraint("tenant_id", "skill_id", name="uq_skill_tenant_skill_id"),)

    id: str = Field(default_factory=lambda: new_id("skill"), primary_key=True)
    tenant_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    version: str = "1.0.0"
    name: str
    business_domain: Optional[str] = None
    description: Optional[str] = None
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="draft", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillVersion(SQLModel, table=True):
    __tablename__ = "skill_versions"
    __table_args__ = (UniqueConstraint("tenant_id", "skill_id", "version", name="uq_skill_version"),)

    id: str = Field(default_factory=lambda: new_id("skillver"), primary_key=True)
    tenant_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    version: str = Field(index=True)
    name: str
    business_domain: Optional[str] = None
    description: Optional[str] = None
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="draft", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentSkillBranch(SQLModel, table=True):
    __tablename__ = "agent_skill_branches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "skill_id", name="uq_agent_skill_branch"),
    )

    id: str = Field(default_factory=lambda: new_id("agentbranch"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    source_skill_id: str = Field(index=True)
    base_version: str = "1.0.0"
    head_version: str = "1.0.0"
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="active", index=True)
    sync_state: str = Field(default="synced", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentSkillBranchVersion(SQLModel, table=True):
    __tablename__ = "agent_skill_branch_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "skill_id", "version", name="uq_agent_skill_branch_version"),
    )

    id: str = Field(default_factory=lambda: new_id("agentbranchver"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    source_skill_id: str = Field(index=True)
    version: str = Field(index=True)
    base_version: str = "1.0.0"
    content_json: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(default="active", index=True)
    sync_state: str = Field(default="diverged", index=True)
    change_summary: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class GeneralSkill(SQLModel, table=True):
    __tablename__ = "general_skills"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", name="uq_general_skill_tenant_slug"),)

    id: str = Field(default_factory=lambda: new_id("genskill"), primary_key=True)
    tenant_id: str = Field(index=True)
    slug: str = Field(index=True)
    name: str
    description: Optional[str] = None
    homepage: Optional[str] = None
    skill_markdown: str
    skill_files_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="draft", index=True)
    capability_scope: str = Field(default="general", index=True)
    permissions_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    runtime_config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeBase(SQLModel, table=True):
    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_knowledge_base_tenant_name"),)

    id: str = Field(default_factory=lambda: new_id("kb"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    status: str = Field(default="active", index=True)
    capability_scope: str = Field(default="general", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeBaseVersion(SQLModel, table=True):
    __tablename__ = "knowledge_base_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "knowledge_base_id", "version", name="uq_knowledge_base_version"),
    )

    id: str = Field(default_factory=lambda: new_id("kbver"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    version: str = Field(default="1.0.0", index=True)
    name: str
    description: Optional[str] = None
    status: str = Field(default="active", index=True)
    capability_scope: str = Field(default="general", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentKnowledgeBranch(SQLModel, table=True):
    __tablename__ = "agent_knowledge_branches"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "knowledge_base_id", name="uq_agent_knowledge_branch"),
    )

    id: str = Field(default_factory=lambda: new_id("agentkb"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    base_version: str = "1.0.0"
    head_version: str = "1.0.0"
    status: str = Field(default="active", index=True)
    sync_state: str = Field(default="synced", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocument(SQLModel, table=True):
    __tablename__ = "knowledge_documents"

    id: str = Field(default_factory=lambda: new_id("kdoc"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    filename: str
    file_type: str = Field(index=True)
    title: Optional[str] = None
    status: str = Field(default="processing", index=True)
    # 原始上传内容的 SHA-256，用于同一知识库版本内的重复文件检测。
    source_sha256: Optional[str] = Field(default=None, index=True)
    bucket_count: int = 0
    chunk_count: int = 0
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeBucket(SQLModel, table=True):
    __tablename__ = "knowledge_buckets"

    id: str = Field(default_factory=lambda: new_id("kbucket"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    document_id: str = Field(index=True)
    bucket_key: str = Field(index=True)
    title: str
    summary: str
    token_estimate: int = 0
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeChunk(SQLModel, table=True):
    __tablename__ = "knowledge_chunks"

    id: str = Field(default_factory=lambda: new_id("kchunk"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    document_id: str = Field(index=True)
    bucket_id: str = Field(index=True)
    chunk_index: int = Field(index=True)
    content: str
    summary: Optional[str] = None
    source_ref: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeConcept(SQLModel, table=True):
    __tablename__ = "knowledge_concepts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_version_id",
            "concept_id",
            name="uq_knowledge_concept_version_path",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("kconcept"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    document_id: Optional[str] = Field(default=None, index=True)
    concept_id: str = Field(index=True)
    concept_type: str = Field(index=True)
    title: str
    description: Optional[str] = None
    content_md: str
    frontmatter_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    links_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    citations_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeDiscoverySuggestion(SQLModel, table=True):
    __tablename__ = "knowledge_discovery_suggestions"

    id: str = Field(default_factory=lambda: new_id("kdisc"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    document_id: str = Field(index=True)
    bucket_id: Optional[str] = Field(default=None, index=True)
    suggestion_type: str = Field(index=True)
    title: str
    status: str = Field(default="pending", index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    source_refs_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeIngestJob(SQLModel, table=True):
    __tablename__ = "knowledge_ingest_jobs"

    id: str = Field(default_factory=lambda: new_id("kjob"), primary_key=True)
    tenant_id: str = Field(index=True)
    knowledge_base_id: str = Field(index=True)
    knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    document_id: Optional[str] = Field(default=None, index=True)
    # create: 新建文档；replace: 覆盖 target_document_id 指向的文档
    operation: str = Field(default="create", index=True)
    target_document_id: Optional[str] = Field(default=None, index=True)
    expected_updated_at: Optional[str] = None
    source_sha256: Optional[str] = Field(default=None, index=True)
    source_message_id: Optional[str] = Field(default=None, index=True)
    proposal_id: Optional[str] = Field(default=None, index=True)
    filename: str
    status: str = Field(default="queued", index=True)
    stage: str = "queued"
    progress: float = 0.0
    error: Optional[str] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeUpdateProposal(SQLModel, table=True):
    """对话中的知识库新增/覆盖提案；确认前不得写入知识库。"""

    __tablename__ = "knowledge_update_proposals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "target_knowledge_base_id",
            "source_sha256",
            "source_message_id",
            name="uq_knowledge_update_proposal_source",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("kprop"), primary_key=True)
    tenant_id: str = Field(index=True)
    confirmation_code: Optional[str] = Field(default=None, index=True)
    agent_id: str = Field(index=True)
    session_id: str = Field(index=True)
    binding_id: Optional[str] = Field(default=None, index=True)
    channel: str = Field(default="web", index=True)
    external_user_id: Optional[str] = Field(default=None)
    external_account_scope: Optional[str] = Field(default=None)
    source_message_id: str = Field(index=True)
    attachment_id: str
    source_sha256: str = Field(index=True)
    filename: str
    content_type: Optional[str] = None
    size: int = 0
    target_knowledge_base_id: str = Field(index=True)
    target_knowledge_base_version_id: Optional[str] = Field(default=None, index=True)
    target_document_id: Optional[str] = Field(default=None, index=True)
    target_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    expected_updated_at: Optional[str] = None
    proposed_action: str = Field(default="undecided", index=True)
    candidates_json: list[dict[str, Any]] = Field(
        default_factory=list, sa_column=Column(JSON)
    )
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="pending", index=True)
    job_id: Optional[str] = Field(default=None, index=True)
    confirmed_at: Optional[datetime] = None
    confirmed_by_user_id: Optional[str] = None
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelConfig(SQLModel, table=True):
    __tablename__ = "model_configs"

    id: str = Field(default_factory=lambda: new_id("model"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    provider: str = "openai_compatible"
    api_protocol: str = Field(default="openai_chat_completions", index=True)
    base_url: Optional[str] = None
    api_key_encrypted: str
    model: str
    temperature: float = 0.2
    max_output_tokens: int = 8192
    extra_body_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    protocol_options_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    legacy_unmapped_options_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    trust_status: str = Field(default="unverified", index=True)
    verified_at: Optional[datetime] = None
    verified_fingerprint: Optional[str] = None
    verification_attempt_id: Optional[str] = None
    verification_started_at: Optional[datetime] = None
    verification_attempt_status: str = Field(default="idle", index=True)
    verification_attempt_error_code: Optional[str] = None
    config_revision: int = 1
    security_revision: int = 1
    key_revision: int = 1
    is_default: bool = False
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PersonaConfig(SQLModel, table=True):
    __tablename__ = "persona_configs"

    tenant_id: str = Field(primary_key=True)
    system_prompt: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UIConfig(SQLModel, table=True):
    __tablename__ = "ui_configs"

    tenant_id: str = Field(primary_key=True)
    show_thinking_trace: bool = True
    show_skill_trace: bool = True
    show_tool_trace: bool = True
    reflection_max_rounds: int = 1
    agent_loop_max_actions: int = 32
    context_token_budget: int = 32_000
    context_compaction_trigger_ratio: float = 0.70
    context_recent_round_limit: int = 6
    context_long_summary_token_budget: int = 4_000
    context_medium_summary_token_budget: int = 4_000
    context_allowed_roles: list[str] = Field(
        default_factory=lambda: ["user", "assistant"],
        sa_column=Column(JSON),
    )
    context_long_summary_prefix: str = "历史的信息可以被总结为："
    context_medium_summary_prefix: str = "近期的历史信息总结为："
    sandbox_enabled: bool = False
    sandbox_network_mode: str = Field(default="all")
    sandbox_allowed_domains: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    harness_storage_path: Optional[str] = None
    # NULL = no 运行设置 override yet; Honcho falls back to HONCHO_* env.
    honcho_enabled: Optional[bool] = Field(default=None)
    honcho_base_url: Optional[str] = Field(default=None)
    honcho_timeout_ms: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentProfile(SQLModel, table=True):
    __tablename__ = "agent_profiles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_agent_profile_tenant_name"),)

    id: str = Field(default_factory=lambda: new_id("agent"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    persona_prompt: Optional[str] = None
    is_overall: bool = Field(default=False, index=True)
    status: str = Field(default="active", index=True)
    harness_max_actions: int = Field(default=32)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentUsage(SQLModel, table=True):
    __tablename__ = "agent_usages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", "agent_id", name="uq_agent_usage_user_agent"),
    )

    id: str = Field(default_factory=lambda: new_id("agentuse"), primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentModelBinding(SQLModel, table=True):
    __tablename__ = "agent_model_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "role", name="uq_agent_model_binding"),
    )

    id: str = Field(default_factory=lambda: new_id("agentmodel"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    role: str = Field(default="default", index=True)
    model_config_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AgentResourceBinding(SQLModel, table=True):
    __tablename__ = "agent_resource_bindings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "resource_type", "resource_id", name="uq_agent_resource"),
    )

    id: str = Field(default_factory=lambda: new_id("agentres"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    resource_type: str = Field(index=True)
    resource_id: str = Field(index=True)
    status: str = Field(default="active", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeCabinetEntry(SQLModel, table=True):
    """Per-employee working files. Not copied with skills, login recipes, or agents."""

    __tablename__ = "employee_cabinet_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "path", name="uq_employee_cabinet_path"),
        Index("ix_employee_cabinet_agent_parent", "tenant_id", "agent_id", "parent_path"),
    )

    id: str = Field(default_factory=lambda: new_id("cab"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    kind: str = Field(index=True)
    name: str
    path: str
    parent_path: str = Field(default="", index=True)
    size_bytes: int = Field(default=0)
    content_type: str = Field(default="application/octet-stream")
    sha256: Optional[str] = None
    source: str = Field(default="console", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeMailbox(SQLModel, table=True):
    """Per-employee IMAP/SMTP identity. Not a login secret and not copied with agents."""

    __tablename__ = "employee_mailboxes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", name="uq_employee_mailbox_agent"),
    )

    id: str = Field(default_factory=lambda: new_id("mbox"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    email_address: str
    imap_host: str
    imap_port: int = Field(default=993)
    imap_encryption: str = Field(default="ssl")
    smtp_host: str
    smtp_port: int = Field(default=587)
    smtp_encryption: str = Field(default="starttls")
    username: str
    password_encrypted: str
    enabled: bool = Field(default=True, index=True)
    last_synced_at: Optional[datetime] = None
    last_error: Optional[str] = None
    uid_validity: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeMailMessage(SQLModel, table=True):
    """Inbox cache, product sent log, and pending confirm drafts. Not copied with agents."""

    __tablename__ = "employee_mail_messages"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "folder", "dedupe_key", name="uq_employee_mail_dedupe"
        ),
        Index("ix_employee_mail_agent_folder", "tenant_id", "agent_id", "folder"),
    )

    id: str = Field(default_factory=lambda: new_id("mail"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    folder: str = Field(index=True)
    status: str = Field(index=True)
    direction: str = Field(default="outbound", index=True)
    source: str = Field(default="console", index=True)
    imap_uid: Optional[str] = None
    rfc_message_id: Optional[str] = None
    dedupe_key: str
    from_address: str = ""
    to_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    cc_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    bcc_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    subject: str = ""
    body_text: str = ""
    in_reply_to: Optional[str] = None
    sent_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    smtp_error: Optional[str] = None
    imap_append_note: Optional[str] = None
    attachments_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    confirm_required: bool = False
    confirmed_at: Optional[datetime] = None
    triage_disposition: Optional[str] = Field(default=None, index=True)
    triage_state: Optional[str] = Field(default=None, index=True)
    triage_reason: Optional[str] = None
    triage_skill_id: Optional[str] = None
    triage_skill_name: Optional[str] = None
    triage_notified_at: Optional[datetime] = None
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeMailTriageRule(SQLModel, table=True):
    """Owner teaching for inbound mail. Per-employee; not copied with agents."""

    __tablename__ = "employee_mail_triage_rules"
    __table_args__ = (
        Index(
            "ix_employee_mail_triage_rules_agent_from",
            "tenant_id",
            "agent_id",
            "from_address",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("mrule"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    from_address: str = Field(index=True)
    disposition: str = Field(index=True)
    skill_id: Optional[str] = None
    skill_slug: Optional[str] = None
    instruction: str = ""
    source_message_id: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeSecret(SQLModel, table=True):
    """Per-employee encrypted login credential. Not copied with agents, skills, or plaza."""

    __tablename__ = "employee_secrets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "name", name="uq_employee_secret_name"),
        Index("ix_employee_secret_agent", "tenant_id", "agent_id"),
    )

    id: str = Field(default_factory=lambda: new_id("esec"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    name: str
    description: str = ""
    secret_type: str = Field(index=True)
    value_encrypted: str
    linked_login_guide_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EmployeeLoginGuide(SQLModel, table=True):
    """Structured login steps for one employee. Plaza copy clones steps only, never secrets."""

    __tablename__ = "employee_login_guides"
    __table_args__ = (
        UniqueConstraint("tenant_id", "agent_id", "name", name="uq_employee_login_guide_name"),
        Index("ix_employee_login_guide_agent", "tenant_id", "agent_id"),
    )

    id: str = Field(default_factory=lambda: new_id("elogin"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    name: str
    url: str
    username_selector: str = ""
    username_label: str = "用户名"
    password_selector: str = ""
    password_label: str = "密码"
    submit_selector: str = ""
    submit_label: str = "登录"
    default_secret_name: Optional[str] = None
    published_to_gallery: bool = Field(default=False, index=True)
    copied_from_id: Optional[str] = None
    guide_type: str = Field(default="web", index=True)
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Contact(SQLModel, table=True):
    """员工通讯录里的真人权威行。渠道外部 id 是可空次级查找键，不是主键。

    primary_user_id 懒连到 User.id：表单不要求已有账号。入站只在已有联系人时回填。
    """

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "agent_id", "wecom_userid", name="uq_contact_agent_wecom_userid"
        ),
        UniqueConstraint(
            "tenant_id", "agent_id", "feishu_openid", name="uq_contact_agent_feishu_openid"
        ),
        UniqueConstraint(
            "tenant_id",
            "agent_id",
            "dingtalk_userid",
            name="uq_contact_agent_dingtalk_userid",
        ),
        UniqueConstraint(
            "tenant_id", "agent_id", "primary_user_id", name="uq_contact_agent_primary_user"
        ),
        Index("ix_contact_agent", "tenant_id", "agent_id"),
        Index("ix_contact_primary_user", "tenant_id", "primary_user_id"),
        Index("ix_contact_wecom_userid", "tenant_id", "wecom_userid"),
        Index("ix_contact_feishu_openid", "tenant_id", "feishu_openid"),
        Index("ix_contact_dingtalk_userid", "tenant_id", "dingtalk_userid"),
    )

    id: str = Field(default_factory=lambda: new_id("contact"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    title: Optional[str] = None
    city: Optional[str] = None
    notes: Optional[str] = None
    wecom_userid: Optional[str] = None
    feishu_openid: Optional[str] = None
    dingtalk_userid: Optional[str] = None
    primary_user_id: Optional[str] = Field(
        default=None, foreign_key="users.id", ondelete="SET NULL", index=True
    )
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


# 根部门的 parent_id。空字符串才能让 UNIQUE(tenant_id, parent_id, name) 对根生效；
# SQLite 里 NULL 不等于 NULL，可空 parent_id 挡不住两个同名根。接口仍把根显示为 null。
ORG_ROOT_PARENT_ID = ""


class OrgDepartment(SQLModel, table=True):
    """租户级部门。邻接表 parent_id，加上物化路径，不用闭包表。

    path 形如 /{id} 或 {parent.path}/{id}。移动子树时改写前缀。
    根的 parent_id 是空字符串，不是 NULL，也不是外键。
    """

    __tablename__ = "org_departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "path", name="uq_org_department_path"),
        UniqueConstraint(
            "tenant_id", "parent_id", "name", name="uq_org_department_sibling_name"
        ),
        Index("ix_org_department_parent", "tenant_id", "parent_id"),
    )

    id: str = Field(default_factory=lambda: new_id("dept"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    parent_id: str = Field(default=ORG_ROOT_PARENT_ID, nullable=False, index=True)
    path: str = Field(index=True)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OrgAffiliation(SQLModel, table=True):
    """人与部门的任职。人键是 Contact.id，不是 User.id。"""

    __tablename__ = "org_affiliations"
    __table_args__ = (
        Index("ix_org_affiliation_department", "tenant_id", "department_id"),
        Index("ix_org_affiliation_contact", "tenant_id", "contact_id"),
    )

    id: str = Field(default_factory=lambda: new_id("orgaff"), primary_key=True)
    tenant_id: str = Field(index=True)
    department_id: str = Field(foreign_key="org_departments.id", index=True)
    contact_id: str = Field(foreign_key="contacts.id", index=True)
    valid_from: date
    valid_to: Optional[date] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class OrgDepartmentHead(SQLModel, table=True):
    """部门负责人，可带任职区间。同一天多条时取 valid_from 最晚的一条。"""

    __tablename__ = "org_department_heads"
    __table_args__ = (
        Index("ix_org_department_head_department", "tenant_id", "department_id"),
        Index("ix_org_department_head_contact", "tenant_id", "contact_id"),
    )

    id: str = Field(default_factory=lambda: new_id("orghead"), primary_key=True)
    tenant_id: str = Field(index=True)
    department_id: str = Field(foreign_key="org_departments.id", index=True)
    contact_id: str = Field(foreign_key="contacts.id", index=True)
    valid_from: date
    valid_to: Optional[date] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Tool(SQLModel, table=True):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_tool_tenant_name"),)

    id: str = Field(default_factory=lambda: new_id("tool"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str = Field(index=True)
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = Field(default="未分桶", index=True)
    tool_type: str = Field(default="http", index=True)
    method: str
    url: str
    headers_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    auth_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    input_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    output_schema: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    allowed_skills_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    mcp_server_id: Optional[str] = Field(default=None, index=True)
    login_guide_id: Optional[str] = Field(default=None, index=True)
    capability_scope: str = Field(default="general", index=True)
    capability_scope_inherited: bool = True
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MCPServer(SQLModel, table=True):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_mcp_server_tenant_name"),)

    id: str = Field(default_factory=lambda: new_id("mcpsrv"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str = Field(index=True)
    display_name: Optional[str] = None
    description: Optional[str] = None
    bucket: str = Field(default="MCP 工具", index=True)
    # 连接方式：stdio / streamable_http / sse / builtin
    transport: str = Field(default="streamable_http", index=True)
    # streamable_http / sse 使用
    url: Optional[str] = None
    headers_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # stdio 使用
    command: Optional[str] = None
    args_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    env_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    cwd: Optional[str] = None
    # MCP Apps is opt-in so legacy standard MCP servers retain identical behavior.
    apps_mode: str = Field(default="disabled", index=True)
    negotiated_capabilities_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
    )
    # 最近一次发现的原始工具定义（预览/审计用）
    discovered_tools_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    last_synced_at: Optional[datetime] = None
    capability_scope: str = Field(default="general", index=True)
    enabled: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class MockOrder(SQLModel, table=True):
    __tablename__ = "mock_orders"

    order_id: str = Field(primary_key=True)
    user_id: Optional[str] = Field(default=None, index=True)
    product_id: Optional[str] = Field(default=None, index=True)
    sku_id: Optional[str] = None
    quantity: int = 1
    status: str = Field(default="created", index=True)
    payment_status: Optional[str] = None
    order_status: Optional[str] = None
    signed_days: int = 0
    refundable: bool = True
    total_amount: float = 0.0
    currency: str = "CNY"
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatSession(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    title: Optional[str] = None
    active_skill_id: Optional[str] = None
    active_step_id: Optional[str] = None
    slots_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    skill_stack_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    pending_tasks_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    resume_after_answer_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    awaiting_input_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    knowledge_context_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    context_state_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    summary: Optional[str] = None
    last_agent_question: Optional[str] = None
    status: str = "active"
    channel: Optional[str] = None
    external_conv_id: Optional[str] = None
    channel_target_json: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    # 渠道会话直挂绑定:出站 staging 优先按它直查,不再靠 (agent_id, channel) 反查
    channel_binding_id: Optional[str] = None
    # 渠道外部账号稳定键:绑定删除后仍保留,仅允许同一外部 Bot 精确认领历史会话
    channel_account_key: Optional[str] = Field(default=None, index=True)
    # 团队会话挂接:非空表示该会话属于某团队(TL 对话/任务执行等)
    team_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelBinding(SQLModel, table=True):
    __tablename__ = "channel_bindings"

    id: str = Field(default_factory=lambda: new_id("chan"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    channel: str = Field(default="wechat", index=True)
    # 用户可编辑的接入显示名;为空时前端回退展示渠道类型名
    name: Optional[str] = Field(default=None)
    # 团队绑定:非空表示该渠道接入某团队(与员工挂载互斥),入站消息直路由团队 TL;
    # 存 team_id 不存 leader,换帅自动跟随
    team_id: Optional[str] = Field(default=None, index=True)
    # pending/active/expired/disabled
    status: str = Field(default="pending", index=True)
    # Fernet 加密后的渠道凭证（如微信 bot_token），绝不回传明文
    credentials_enc: Optional[str] = None
    # ilink_bot_id、baseurl、get_updates_buf 游标、session_expired、bound_at 等
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # provider 侧 Bot 的稳定连接键,全部署唯一;pending 绑定激活前允许为空
    external_account_key: Optional[str] = Field(default=None, unique=True, index=True)
    # 身份作用域稳定键:企微为 corp_id,微信为空字符串
    identity_scope_key: Optional[str] = Field(default=None, index=True)
    # provider 回调声明的租户边界；飞书首次可信事件中 CAS 固定 tenant_key
    provider_tenant_key: Optional[str] = Field(default=None, index=True)
    # 每次凭证/账号配置成功提交后递增,用于 ingress 代际隔离
    config_revision: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    connected: bool = False
    # 最近一次成功连上渠道的时间(企微断开超时告警的时间基准)
    last_connected_at: Optional[datetime] = None
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WeChatKfAccount(SQLModel, table=True):
    """客服账号到 StaffDeck 路由的映射；一个 API binding 可管理多个账号。"""

    __tablename__ = "wechat_kf_accounts"
    __table_args__ = (
        UniqueConstraint("binding_id", "open_kfid", name="uq_wechat_kf_account_binding_kfid"),
        UniqueConstraint("tenant_id", "open_kfid", name="uq_wechat_kf_account_tenant_kfid"),
    )

    id: str = Field(default_factory=lambda: new_id("wka"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    open_kfid: str = Field(index=True)
    name: str = ""
    agent_id: Optional[str] = Field(default=None, index=True)
    team_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="active", index=True)
    sync_cursor: str = ""
    last_error: Optional[str] = None
    last_sync_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelBindingAgent(SQLModel, table=True):
    """渠道账号可调度的员工集合（一个微信号挂载多个数字员工，恰好一个默认）。"""

    __tablename__ = "channel_binding_agents"
    __table_args__ = (UniqueConstraint("binding_id", "agent_id", name="uq_channel_binding_agent"),)

    id: str = Field(default_factory=lambda: new_id("chba"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    is_default: bool = False
    sort_order: int = 0
    created_at: datetime = Field(default_factory=utc_now)


class ChannelBindingManager(SQLModel, table=True):
    """渠道绑定协作者:创建者/admin 显式授权的非创建者,可凭证/挂载/启停但不能删除。

    同一 (binding, user) 仅一行;移除即软撤销(revoked_at),重新添加复活该行,
    保留最近一次授权/撤销记录用于审计。删除渠道绑定级联清空协作者行。
    """

    __tablename__ = "channel_binding_managers"
    __table_args__ = (
        UniqueConstraint("binding_id", "user_id", name="uq_channel_binding_manager"),
        Index("ix_channel_binding_managers_tenant_user", "tenant_id", "user_id"),
    )

    id: str = Field(default_factory=lambda: new_id("chbm"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    user_id: str = Field(index=True)
    granted_by_user_id: str
    granted_at: datetime = Field(default_factory=utc_now)
    revoked_at: Optional[datetime] = None


class ChannelConvState(SQLModel, table=True):
    """路由指针：每个 (binding, external_conv_id) 会话的当前员工。"""

    __tablename__ = "channel_conv_states"
    __table_args__ = (
        UniqueConstraint("binding_id", "external_conv_id", name="uq_channel_conv_state"),
    )

    id: str = Field(default_factory=lambda: new_id("chconv"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    external_conv_id: str
    current_agent_id: str
    # 路由指针版本；自动路由分类完成后必须以此做 CAS，避免覆盖期间的手动切换。
    routing_revision: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    # 手动 /切换 后的保护窗:此时间之前跳过智能自动分发
    manual_pin_until: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelBindCode(SQLModel, table=True):
    """渠道身份自助绑定码:网页端生成,渠道侧 /绑定 <码> 核销。"""

    __tablename__ = "channel_bind_codes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "code", name="uq_channel_bind_code_tenant_code"),
        UniqueConstraint("tenant_id", "user_id", name="uq_channel_bind_code_tenant_user"),
    )

    id: str = Field(default_factory=lambda: new_id("chbc"), primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    code: str = Field(index=True)
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


class ChannelIdentity(SQLModel, table=True):
    __tablename__ = "channel_identities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel",
            "external_account_scope",
            "external_user_id",
            name="uq_channel_identity_scope_external",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("chident"), primary_key=True)
    tenant_id: str = Field(index=True)
    channel: str = Field(index=True)
    # 渠道账号作用域:wechat 置空(全局 wxid);wecom 取 corp_id/bot_id/binding.id,隔离跨企业身份
    external_account_scope: str = Field(default="", index=True)
    external_user_id: str
    staffdeck_user_id: str = Field(index=True)
    display_name: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelInboundEvent(SQLModel, table=True):
    __tablename__ = "channel_inbound_events"
    __table_args__ = (
        UniqueConstraint("binding_id", "event_id", name="uq_channel_inbound_event_binding"),
        Index(
            "ix_channel_inbound_events_binding_status_created",
            "binding_id",
            "status",
            "created_at",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("chevt"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    channel: str = Field(index=True)
    event_id: str
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # 入站时的绑定配置代次，仅用于 ingress 代际审计；已落库事件不因后续轮换失效
    config_revision: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    # 每条事件不可变的回复目标；异步处理不得读取会话上的可变 target
    target_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, server_default="{}"),
    )
    # 收到确认标记的句柄；最终回复送达后据此异步撤回。飞书存远端 reaction_id；
    # 钉钉 emotion 接口不返回 ID，存本地哨兵值表示"已挂上待撤回"。
    reaction_id: Optional[str] = Field(default=None, index=True)
    # received/processing/done/failed
    status: str = Field(default="received", index=True)
    # 创建/接管该事件的进程启动代次；当前代次仍在运行时禁止按墙钟误接管。
    processor_run_id: Optional[str] = Field(default=None, index=True)
    processor_lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    error: Optional[str] = None
    processed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelDelivery(SQLModel, table=True):
    __tablename__ = "channel_deliveries"

    id: str = Field(default_factory=lambda: new_id("chdlv"), primary_key=True)
    tenant_id: str = Field(index=True)
    binding_id: str = Field(index=True)
    session_id: str = Field(index=True)
    message_id: Optional[str] = Field(default=None, index=True)
    # 渠道远端返回的消息 ID；与本地 message_id 分开保存，避免覆盖本地关联。
    remote_message_id: Optional[str] = Field(default=None, index=True)
    # 投递目标：to_user_id + context_token
    target_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # reply/file/error_notice
    kind: str = Field(default="reply", index=True)
    text: str
    # kind=file 时的产物引用（task_frame_id/path/display_name/size/sha256/share_token）
    artifact_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # pending/sending/delivered/failed
    status: str = Field(default="pending", index=True)
    attempts: int = 0
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    # 原子 claim 的抢占时间(守护据此重置卡死投递)
    sending_since: Optional[datetime] = None
    # 每次领取投递都会生成新的 owner 并递增 generation；旧 worker 的迟到结果不得落库。
    delivery_owner: Optional[str] = Field(default=None, index=True)
    delivery_generation: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    last_error: Optional[str] = None
    # 回复类投递 = message_id，天然幂等
    idempotency_key: str = Field(unique=True, index=True)
    # 第一次真正尝试远端发送的时间，用于飞书 UUID 一小时去重窗口
    first_attempt_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HumanHandoffRequest(SQLModel, table=True):
    __tablename__ = "human_handoff_requests"

    id: str = Field(default_factory=lambda: new_id("handoff"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    agent_id: Optional[str] = Field(default=None, index=True)
    requester_user_id: Optional[str] = Field(default=None, index=True)
    assignee_user_id: Optional[str] = Field(default=None, index=True)
    trigger_skill_id: Optional[str] = Field(default=None, index=True)
    trigger_step_id: Optional[str] = Field(default=None, index=True)
    context_summary: Optional[str] = None
    pending_question: Optional[str] = None
    status: str = Field(default="pending", index=True)
    human_reply: Optional[str] = None
    resume_payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    answered_at: Optional[datetime] = None
    # 飞书 handoff_notice 投递成功后回写的飞书 message_id;阶段 4 据此关联处理人回复。
    # 网页触发的 handoff 无此字段(为空),不影响现有网页回复链路。
    notify_message_id: Optional[str] = Field(default=None, index=True)


class ScheduledTask(SQLModel, table=True):
    __tablename__ = "scheduled_tasks"

    id: str = Field(default_factory=lambda: new_id("sched"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    created_by_user_id: str = Field(index=True)
    title: str
    prompt: str
    description: Optional[str] = None
    schedule_type: str = Field(default="daily", index=True)
    schedule_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    timezone: str = Field(default="Asia/Shanghai", index=True)
    rrule: Optional[str] = None
    status: str = Field(default="active", index=True)
    concurrency_policy: str = Field(default="forbid", index=True)
    misfire_policy: str = Field(default="coalesce", index=True)
    max_runs: Optional[int] = None
    end_at: Optional[datetime] = Field(default=None, index=True)
    next_run_at: Optional[datetime] = Field(default=None, index=True)
    last_run_at: Optional[datetime] = Field(default=None, index=True)
    last_status: Optional[str] = Field(default=None, index=True)
    run_count: int = 0
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_until: Optional[datetime] = Field(default=None, index=True)
    source_session_id: Optional[str] = Field(default=None, index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScheduledTaskRun(SQLModel, table=True):
    __tablename__ = "scheduled_task_runs"
    __table_args__ = (
        UniqueConstraint("scheduled_task_id", "scheduled_for", name="uq_scheduled_task_run_due_time"),
    )

    id: str = Field(default_factory=lambda: new_id("schedrun"), primary_key=True)
    tenant_id: str = Field(index=True)
    scheduled_task_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    user_id: str = Field(index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    scheduled_for: datetime = Field(index=True)
    status: str = Field(default="queued", index=True)
    started_at: Optional[datetime] = Field(default=None, index=True)
    finished_at: Optional[datetime] = Field(default=None, index=True)
    result_summary: Optional[str] = None
    error: Optional[str] = None
    trace_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HarnessAgentLoopRecord(SQLModel, table=True):
    """Durable logical AgentLoop shared across Harness activations."""

    __tablename__ = "harness_agent_loops"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "loop_key", name="uq_harness_agent_loop_session_key"
        ),
    )

    id: str = Field(default_factory=lambda: new_id("hloop"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    loop_key: str = Field(index=True)
    kind: str = Field(default="general", index=True)
    status: str = Field(default="active", index=True)
    owner_task_frame_record_id: Optional[str] = Field(default=None, index=True)
    skill_id: Optional[str] = Field(default=None, index=True)
    workspace_scope_id: Optional[str] = Field(default=None, index=True)
    checkpoint_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    last_run_id: Optional[str] = Field(default=None, index=True)
    state_version: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None


class HarnessTaskFrameRecord(SQLModel, table=True):
    """Durable TaskFrame state for the isolated Harness v2 execution path."""

    __tablename__ = "harness_task_frames"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "task_id", name="uq_harness_task_frame_session_task"
        ),
    )

    id: str = Field(default_factory=lambda: new_id("htask"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    source_turn_id: str = Field(index=True)
    task_id: str = Field(index=True)
    agent_loop_id: Optional[str] = Field(default=None, index=True)
    kind: str = Field(default="conversation", index=True)
    decision: str = Field(default="answer_only", index=True)
    status: str = Field(default="queued", index=True)
    sequence: int = 0
    skill_id: Optional[str] = Field(default=None, index=True)
    step_id: Optional[str] = Field(default=None, index=True)
    user_intent: Optional[str] = None
    requirements_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    slots_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    depends_on_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    task_requirement_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    state_version: int = 1
    attempt_no: int = 0
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HarnessRunRecord(SQLModel, table=True):
    __tablename__ = "harness_runs"

    id: str = Field(default_factory=lambda: new_id("hrun"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    task_frame_record_id: str = Field(index=True)
    agent_loop_id: Optional[str] = Field(default=None, index=True)
    task_id: str = Field(index=True)
    source_turn_id: str = Field(index=True)
    status: str = Field(default="running", index=True)
    attempt_no: int = 1
    lease_owner: Optional[str] = Field(default=None, index=True)
    lease_expires_at: Optional[datetime] = Field(default=None, index=True)
    action_count: int = 0
    task_requirement_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    capability_snapshot_json: dict[str, Any] = Field(
        default_factory=dict, sa_column=Column(JSON)
    )
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HarnessTurnRecord(SQLModel, table=True):
    """Exactly-once receipt for one client-addressable Harness turn."""

    __tablename__ = "harness_turns"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "client_turn_id",
            name="uq_harness_turn_client_receipt",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("hturn"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    client_turn_id: str = Field(index=True)
    request_digest: str = Field(index=True)
    status: str = Field(default="started", index=True)
    lease_owner: str = Field(index=True)
    lease_expires_at: datetime = Field(index=True)
    user_message_id: Optional[str] = Field(default=None, index=True)
    response_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
    )
    error_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
    )
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HarnessSessionLeaseRecord(SQLModel, table=True):
    """Cross-process execution fence for one Harness chat session."""

    __tablename__ = "harness_session_leases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "session_id",
            name="uq_harness_session_execution_lease",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("hslease"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    lease_owner: str = Field(index=True)
    lease_expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class HarnessInvocationRecord(SQLModel, table=True):
    __tablename__ = "harness_invocations"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "call_id", name="uq_harness_invocation_run_call"
        ),
    )

    id: str = Field(default_factory=lambda: new_id("hinvoke"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    task_id: str = Field(index=True)
    run_id: str = Field(index=True)
    call_id: str = Field(index=True)
    tool_name: str = Field(index=True)
    request_digest: str = Field(index=True)
    logical_action_key: Optional[str] = Field(
        default=None,
        unique=True,
        index=True,
    )
    replayed_from_invocation_id: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="started", index=True)
    arguments_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    result_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    response_cache_json: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSON),
    )
    approval_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(default_factory=lambda: new_id("msg"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    role: str
    content: str
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class MessageFeedback(SQLModel, table=True):
    __tablename__ = "message_feedback"
    __table_args__ = (UniqueConstraint("tenant_id", "message_id", "user_id", name="uq_feedback_message_user"),)

    id: str = Field(default_factory=lambda: new_id("fb"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    message_id: str = Field(index=True)
    user_id: str = Field(index=True)
    rating: str = Field(index=True)
    analysis_status: str = Field(default="pending", index=True)
    analysis_bucket: Optional[str] = Field(default=None, index=True)
    analysis_reason: Optional[str] = None
    analysis_summary: Optional[str] = None
    analysis_confidence: Optional[float] = None
    analysis_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    analyzed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class SkillFeedback(SQLModel, table=True):
    __tablename__ = "skill_feedback"
    __table_args__ = (UniqueConstraint("tenant_id", "message_id", "user_id", name="uq_skill_feedback_message_user"),)

    id: str = Field(default_factory=lambda: new_id("skillfb"), primary_key=True)
    tenant_id: str = Field(index=True)
    skill_id: str = Field(index=True)
    skill_version: Optional[str] = Field(default=None, index=True)
    step_id: Optional[str] = Field(default=None, index=True)
    session_id: str = Field(index=True)
    message_id: str = Field(index=True)
    user_id: str = Field(index=True)
    rating: str = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class EvolutionProposal(SQLModel, table=True):
    __tablename__ = "evolution_proposals"

    id: str = Field(default_factory=lambda: new_id("evo"), primary_key=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    resource_type: str = Field(index=True)
    resource_id: str = Field(index=True)
    resource_key: str = Field(index=True)
    resource_name: str
    base_version: Optional[str] = Field(default=None, index=True)
    status: str = Field(default="ready_for_review", index=True)
    trigger_type: str = Field(default="feedback", index=True)
    risk_level: str = Field(default="medium", index=True)
    hypothesis: str = ""
    rationale: str = ""
    expected_outcome: str = ""
    source_feedback_ids_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    candidate_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    diff_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    evaluation_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    published_snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    created_by_user_id: str = Field(index=True)
    reviewed_by_user_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    reviewed_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None


class AgentEvent(SQLModel, table=True):
    __tablename__ = "agent_events"

    id: str = Field(default_factory=lambda: new_id("evt"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    event_type: str = Field(index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class MemoryRecord(SQLModel, table=True):
    __tablename__ = "memories"

    id: str = Field(default_factory=lambda: new_id("mem"), primary_key=True)
    tenant_id: str = Field(index=True)
    user_id: str = Field(index=True)
    username: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    kind: str = Field(default="conversation", index=True)
    content: str
    importance: float = 0.5
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Team(SQLModel, table=True):
    """多 Agent 团队:一名 TL(leader 角色成员)+ 若干成员(数字员工)。"""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_team_tenant_name"),)

    id: str = Field(default_factory=lambda: new_id("team"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    description: Optional[str] = None
    owner_user_id: str = Field(index=True)
    # 预留:并发策略/竞标等团队级配置
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamMember(SQLModel, table=True):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "agent_id", name="uq_team_member_agent"),)

    id: str = Field(default_factory=lambda: new_id("team_member"), primary_key=True)
    team_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    # role: leader(TL,每团队至多一名)/ member
    role: str = Field(default="member", index=True)
    created_at: datetime = Field(default_factory=utc_now)


class TeamRun(SQLModel, table=True):
    """One durable TL plan from delegation through final team synthesis."""

    __tablename__ = "team_runs"
    __table_args__ = (
        UniqueConstraint(
            "tl_session_id",
            "source_turn_id",
            name="uq_team_run_tl_session_source_turn",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("team_run"), primary_key=True)
    team_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    tl_session_id: str = Field(index=True)
    source_turn_id: str = Field(index=True)
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    # planning -> running/awaiting_input -> synthesizing -> completed/failed
    status: str = Field(default="planning", index=True)
    synthesis_session_id: Optional[str] = Field(default=None, index=True)
    final_message_id: Optional[str] = Field(default=None, index=True)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


class TeamTask(SQLModel, table=True):
    """团队任务:blocked -> pending -> in_progress -> review -> done/rework/escalated;

    rework -> in_progress 重入;pending -> bidding -> pending 为任务池竞标链路。
    """

    __tablename__ = "team_tasks"

    id: str = Field(default_factory=lambda: new_id("team_task"), primary_key=True)
    team_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    team_run_id: Optional[str] = Field(default=None, index=True)
    source_turn_id: Optional[str] = Field(default=None, index=True)
    parent_task_id: Optional[str] = Field(default=None, index=True)
    title: str
    description: Optional[str] = None
    priority: str = Field(default="normal", index=True)
    status: str = Field(default="pending", index=True)
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    created_by_tl: bool = False
    assignee_agent_id: Optional[str] = Field(default=None, index=True)
    session_id: Optional[str] = Field(default=None, index=True)
    depends_on_task_ids_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    activation_condition_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    report_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    review_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # 乐观锁版本号,人改判/验收并发时防覆盖
    version: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamTaskEvent(SQLModel, table=True):
    """团队任务审计流水。"""

    __tablename__ = "team_task_events"

    id: str = Field(default_factory=lambda: new_id("team_task_event"), primary_key=True)
    task_id: str = Field(index=True)
    team_id: str = Field(index=True)
    # actor_type: user / agent / system
    actor_type: str = Field(index=True)
    actor_id: Optional[str] = Field(default=None, index=True)
    event_type: str = Field(index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)


class TeamWakeEvent(SQLModel, table=True):
    """团队唤醒事件:任务派发/报告/退回时唤醒对应成员或 TL 的后台会话。"""

    __tablename__ = "team_wake_events"

    id: str = Field(default_factory=lambda: new_id("team_wake"), primary_key=True)
    team_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    target_agent_id: str = Field(index=True)
    # trigger_type: task_assigned / task_report / task_rework / tl_message 等
    trigger_type: str = Field(index=True)
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # status: pending -> claimed -> done / failed
    status: str = Field(default="pending", index=True)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamBlackboardEntry(SQLModel, table=True):
    """团队黑板:团队工作记忆,TL 裁决写入/人直写,按团队隔离。"""

    __tablename__ = "team_blackboard_entries"

    id: str = Field(default_factory=lambda: new_id("bbentry"), primary_key=True)
    team_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    content: str
    tags_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    # source_type: member(TL 裁决的成员建议) / leader / human(人直写)
    source_type: str = Field(default="human", index=True)
    source_agent_id: Optional[str] = Field(default=None, index=True)
    source_task_id: Optional[str] = Field(default=None, index=True)
    # 引用回链:如 {"task_id": ..., "task_title": ...}
    citation_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # status: active / archived
    status: str = Field(default="active", index=True)
    pinned: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class TeamTaskBid(SQLModel, table=True):
    """团队任务竞标:候选成员的方案陈述(round=1)/反驳(round=2)及 TL 打分。"""

    __tablename__ = "team_task_bids"
    __table_args__ = (
        UniqueConstraint("task_id", "agent_id", "round", "kind", name="uq_team_task_bid_round"),
    )

    id: str = Field(default_factory=lambda: new_id("bid"), primary_key=True)
    task_id: str = Field(index=True)
    team_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    round: int = 1
    # kind: statement(方案陈述) / rebuttal(反驳)
    kind: str = Field(default="statement", index=True)
    content: str
    # TL 裁决后回写的分数与理由,未裁决前为 None
    score: Optional[float] = None
    score_rationale: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class WecomOaAppCredential(SQLModel, table=True):
    """企微 OA 自建应用凭证（与智能机器人 ChannelBinding 分离）。"""

    __tablename__ = "wecom_oa_app_credentials"
    __table_args__ = (
        UniqueConstraint("tenant_id", "corp_id", name="uq_wecom_oa_app_tenant_corp"),
    )

    id: str = Field(default_factory=lambda: new_id("woaapp"), primary_key=True)
    tenant_id: str = Field(index=True)
    corp_id: str = Field(index=True)
    agentid: Optional[str] = None
    # Fernet 加密的 corpsecret；绝不回传明文
    secret_enc: str
    # 回调验签 Token / EncodingAESKey（可空；生产回调需要）
    callback_token_enc: Optional[str] = None
    callback_aes_key_enc: Optional[str] = None
    status: str = Field(default="active", index=True)
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WecomTempIdentityBinding(SQLModel, table=True):
    """组织架构上线前的临时身份绑定：staffdeck_user_id → 企微 userid。

    TODO: 以后迁到 Contact.wecom_userid。通讯录 v1 不迁移本表，解析顺序也不改。
    """

    __tablename__ = "wecom_temp_identity_bindings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "corp_scope",
            "staffdeck_user_id",
            name="uq_wecom_temp_identity_user",
        ),
    )

    id: str = Field(default_factory=lambda: new_id("woatmp"), primary_key=True)
    tenant_id: str = Field(index=True)
    corp_scope: str = Field(index=True)
    staffdeck_user_id: str = Field(index=True)
    wecom_userid: str
    display_name: Optional[str] = None
    created_by_user_id: Optional[str] = Field(default=None, index=True)
    updated_by_user_id: Optional[str] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class WecomApprovalIntent(SQLModel, table=True):
    """审批代发意图：确认后不可变快照 + 状态机 + 会话/实例绑定。"""

    __tablename__ = "wecom_approval_intents"
    __table_args__ = (
        Index("ix_wecom_approval_intent_session", "tenant_id", "session_id"),
        Index("ix_wecom_approval_intent_sp_no", "tenant_id", "sp_no"),
    )

    id: str = Field(default_factory=lambda: new_id("woaint"), primary_key=True)
    tenant_id: str = Field(index=True)
    session_id: str = Field(index=True)
    agent_id: str = Field(index=True)
    user_id: Optional[str] = Field(default=None, index=True)
    # draft | confirmed | submitting | submitted | failed
    status: str = Field(default="draft", index=True)
    template_id: str
    scenario_key: str = Field(default="default", index=True)
    applicant_mode: str  # fixed | chat_partner
    creator_userid: Optional[str] = None
    creator_display_name: Optional[str] = None
    # 不可变确认载荷：fields、template、mode、userid、digest
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    submission_digest: str = Field(index=True)
    corp_id: str = Field(index=True)
    sp_no: Optional[str] = Field(default=None, index=True)
    failure_reason: Optional[str] = None
    identity_source: Optional[str] = None  # channel_identity | temp_binding
    compliance_checked: bool = False
    compliance_passed: Optional[bool] = None
    # 审计：触发代发的平台用户
    audit_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    last_progress_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    confirmed_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None


class WecomApprovalStatusEvent(SQLModel, table=True):
    """审批状态变更历史（主动查询与回调共享写入路径）。"""

    __tablename__ = "wecom_approval_status_events"
    __table_args__ = (
        UniqueConstraint(
            "intent_id",
            "sp_status",
            "change_event",
            name="uq_wecom_approval_status_event",
        ),
        Index("ix_wecom_approval_status_intent", "intent_id", "created_at"),
    )

    id: str = Field(default_factory=lambda: new_id("woaevt"), primary_key=True)
    tenant_id: str = Field(index=True)
    intent_id: str = Field(index=True)
    session_id: str = Field(index=True)
    sp_no: str = Field(index=True)
    sp_status: str
    change_event: str = Field(default="")
    source: str = Field(default="query")  # query | callback
    payload_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
