from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, col, select

from app.db.models import AgentProfile, EmployeeLoginGuide, EmployeeSecret, utc_now
from app.secrets.errors import SecretError
from app.secrets.executor import LoginAttempt, get_login_executor
from app.secrets.schema import (
    ALLOWED_SECRET_TYPES,
    SECRET_TYPE_PASSWORD,
    SECRET_TYPE_SESSION_SNAPSHOT,
    LoginGuideCopyRequest,
    LoginGuideGalleryItem,
    LoginGuideListResponse,
    LoginGuideRead,
    LoginGuideWriteRequest,
    LoginResult,
    SecretCreateRequest,
    SecretListResponse,
    SecretRead,
    SecretUpdateRequest,
)
from app.security.encryption import decrypt_secret, encrypt_secret
from app.security.permissions import agent_owned_by_user, is_admin_user

CAPTCHA_MESSAGE = "需要人工处理验证码"


def get_agent(db: Session, tenant_id: str, agent_id: str) -> AgentProfile:
    row = db.get(AgentProfile, agent_id)
    if row is None or row.tenant_id != tenant_id:
        raise SecretError("LOGIN_AGENT_NOT_FOUND", "员工不存在。", status_code=404)
    return row


def can_read_secrets(agent: AgentProfile, user: Any) -> bool:
    return is_admin_user(user) or agent_owned_by_user(agent, user)


def can_write_secrets(agent: AgentProfile, user: Any) -> bool:
    return agent_owned_by_user(agent, user)


def ensure_reader(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_read_secrets(agent, user):
        return agent
    raise SecretError("LOGIN_FORBIDDEN", "不能打开该员工的密钥或登录说明。", status_code=403)


def ensure_writer(db: Session, tenant_id: str, agent_id: str, user: Any) -> AgentProfile:
    agent = get_agent(db, tenant_id, agent_id)
    if can_write_secrets(agent, user):
        return agent
    raise SecretError(
        "LOGIN_WRITE_FORBIDDEN",
        "没有权限编辑该员工的密钥或登录说明。",
        status_code=403,
    )


def list_secrets(
    db: Session, tenant_id: str, agent_id: str, *, can_write: bool
) -> SecretListResponse:
    rows = db.exec(
        select(EmployeeSecret)
        .where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
        )
        .order_by(EmployeeSecret.updated_at.desc())
    ).all()
    guides = {item.id: item for item in _guides_for_agent(db, tenant_id, agent_id)}
    return SecretListResponse(
        agent_id=agent_id,
        can_write=can_write,
        secrets=[secret_read(row, guides.get(row.linked_login_guide_id or "")) for row in rows],
    )


def create_secret(
    db: Session, tenant_id: str, agent_id: str, request: SecretCreateRequest
) -> SecretRead:
    name = _require_name(request.name)
    secret_type = _require_type(request.secret_type)
    _ensure_unique_secret_name(db, tenant_id, agent_id, name)
    blob = _encode_secret_value(
        secret_type,
        username=request.username,
        value=request.value,
        required=True,
    )
    row = EmployeeSecret(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name=name,
        description=str(request.description or "").strip(),
        secret_type=secret_type,
        value_encrypted=encrypt_secret(blob),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return secret_read(row)


def update_secret(
    db: Session,
    tenant_id: str,
    agent_id: str,
    secret_id: str,
    request: SecretUpdateRequest,
) -> SecretRead:
    row = _require_secret(db, tenant_id, agent_id, secret_id)
    if request.name is not None:
        name = _require_name(request.name)
        if name != row.name:
            _ensure_unique_secret_name(db, tenant_id, agent_id, name, exclude_id=row.id)
            _rename_secret_bindings(db, tenant_id, agent_id, row.name, name)
            row.name = name
    if request.description is not None:
        row.description = str(request.description).strip()
    if request.value or request.username:
        current = _decode_secret_value(row)
        blob = _encode_secret_value(
            row.secret_type,
            username=request.username if request.username is not None else current.get("username"),
            value=request.value if request.value else current.get("password") or current.get("storage_state"),
            required=True,
        )
        row.value_encrypted = encrypt_secret(blob)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return secret_read(row, _guide_by_id(db, row.linked_login_guide_id))


def delete_secret(db: Session, tenant_id: str, agent_id: str, secret_id: str) -> None:
    row = _require_secret(db, tenant_id, agent_id, secret_id)
    _clear_secret_bindings(db, tenant_id, agent_id, row.name)
    db.delete(row)
    db.commit()


def list_login_guides(
    db: Session, tenant_id: str, agent_id: str, *, can_write: bool
) -> LoginGuideListResponse:
    rows = _guides_for_agent(db, tenant_id, agent_id)
    secrets = _secrets_by_name(db, tenant_id, agent_id)
    return LoginGuideListResponse(
        agent_id=agent_id,
        can_write=can_write,
        guides=[login_guide_read(row, secrets, can_write=can_write) for row in rows],
    )


def create_login_guide(
    db: Session, tenant_id: str, agent_id: str, request: LoginGuideWriteRequest
) -> LoginGuideRead:
    name = _require_name(request.name)
    _ensure_unique_guide_name(db, tenant_id, agent_id, name)
    url = _require_url(request.url)
    default_name = _optional_secret_name(db, tenant_id, agent_id, request.default_secret_name)
    row = EmployeeLoginGuide(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name=name,
        url=url,
        username_selector=str(request.username_selector or "").strip(),
        username_label=str(request.username_label or "用户名").strip() or "用户名",
        password_selector=str(request.password_selector or "").strip(),
        password_label=str(request.password_label or "密码").strip() or "密码",
        submit_selector=str(request.submit_selector or "").strip(),
        submit_label=str(request.submit_label or "登录").strip() or "登录",
        default_secret_name=default_name,
        published_to_gallery=bool(request.published),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    secrets = _secrets_by_name(db, tenant_id, agent_id)
    return login_guide_read(row, secrets, can_write=True)


def update_login_guide(
    db: Session,
    tenant_id: str,
    agent_id: str,
    guide_id: str,
    request: LoginGuideWriteRequest,
) -> LoginGuideRead:
    row = _require_guide(db, tenant_id, agent_id, guide_id)
    name = _require_name(request.name)
    if name != row.name:
        _ensure_unique_guide_name(db, tenant_id, agent_id, name, exclude_id=row.id)
        row.name = name
    row.url = _require_url(request.url)
    row.username_selector = str(request.username_selector or "").strip()
    row.username_label = str(request.username_label or "用户名").strip() or "用户名"
    row.password_selector = str(request.password_selector or "").strip()
    row.password_label = str(request.password_label or "密码").strip() or "密码"
    row.submit_selector = str(request.submit_selector or "").strip()
    row.submit_label = str(request.submit_label or "登录").strip() or "登录"
    row.default_secret_name = _optional_secret_name(
        db, tenant_id, agent_id, request.default_secret_name
    )
    row.published_to_gallery = bool(request.published)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    secrets = _secrets_by_name(db, tenant_id, agent_id)
    return login_guide_read(row, secrets, can_write=True)


def delete_login_guide(db: Session, tenant_id: str, agent_id: str, guide_id: str) -> None:
    row = _require_guide(db, tenant_id, agent_id, guide_id)
    linked = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
            EmployeeSecret.linked_login_guide_id == row.id,
        )
    ).all()
    for secret in linked:
        secret.linked_login_guide_id = None
        secret.updated_at = utc_now()
        db.add(secret)
    db.delete(row)
    db.commit()


def list_gallery(db: Session, tenant_id: str) -> list[LoginGuideGalleryItem]:
    rows = db.exec(
        select(EmployeeLoginGuide)
        .where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.published_to_gallery == True,
        )
        .order_by(col(EmployeeLoginGuide.updated_at).desc())
    ).all()
    agents = {
        item.id: item
        for item in db.exec(
            select(AgentProfile).where(AgentProfile.tenant_id == tenant_id)
        ).all()
    }
    items: list[LoginGuideGalleryItem] = []
    for row in rows:
        agent = agents.get(row.agent_id)
        items.append(
            LoginGuideGalleryItem(
                id=row.id,
                name=row.name,
                url=row.url,
                username_selector=row.username_selector,
                username_label=row.username_label,
                password_selector=row.password_selector,
                password_label=row.password_label,
                submit_selector=row.submit_selector,
                submit_label=row.submit_label,
                source_agent_id=row.agent_id,
                source_agent_name=agent.name if agent is not None else "",
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
            )
        )
    return items


def copy_login_guide(
    db: Session,
    tenant_id: str,
    source_guide_id: str,
    request: LoginGuideCopyRequest,
    user: Any,
) -> LoginGuideRead:
    source = db.get(EmployeeLoginGuide, source_guide_id)
    if source is None or source.tenant_id != tenant_id:
        raise SecretError("LOGIN_GUIDE_NOT_FOUND", "登录说明不存在。", status_code=404)
    if not source.published_to_gallery and not is_admin_user(user):
        raise SecretError("LOGIN_FORBIDDEN", "只能复制已发布到广场的登录说明。", status_code=403)
    ensure_writer(db, tenant_id, request.target_agent_id, user)
    name = _unique_copied_name(db, tenant_id, request.target_agent_id, source.name)
    row = EmployeeLoginGuide(
        tenant_id=tenant_id,
        agent_id=request.target_agent_id,
        name=name,
        url=source.url,
        username_selector=source.username_selector,
        username_label=source.username_label,
        password_selector=source.password_selector,
        password_label=source.password_label,
        submit_selector=source.submit_selector,
        submit_label=source.submit_label,
        default_secret_name=None,
        published_to_gallery=False,
        copied_from_id=source.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    secrets = _secrets_by_name(db, tenant_id, request.target_agent_id)
    return login_guide_read(row, secrets, can_write=True)


def perform_secure_login(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    login_guide_name: str | None = None,
    login_guide_id: str | None = None,
    secret_name: str | None = None,
    persist_snapshot: bool = True,
) -> LoginResult:
    guide = _resolve_guide(db, tenant_id, agent_id, login_guide_id, login_guide_name)
    requested_name = str(secret_name or "").strip() or None
    primary = _resolve_secret(
        db,
        tenant_id,
        agent_id,
        requested_name or guide.default_secret_name,
        required_for=guide.name,
    )
    snapshot = _linked_snapshot(db, tenant_id, agent_id, guide.id)
    password_ciphertext = (
        primary.value_encrypted if primary.secret_type == SECRET_TYPE_PASSWORD else None
    )
    used = primary
    switched = False
    execution = _execute_login(guide, used)
    if execution.captcha_or_2fa:
        if used.secret_type != SECRET_TYPE_SESSION_SNAPSHOT and snapshot is not None:
            used = snapshot
            switched = True
            execution = _execute_login(guide, used)
        elif used.secret_type != SECRET_TYPE_SESSION_SNAPSHOT:
            return LoginResult(
                success=False,
                login_guide_name=guide.name,
                secret_name_used=used.name,
                secret_type_used=used.secret_type,
                captcha_or_2fa=True,
                code="LOGIN_CAPTCHA_REQUIRED",
                message=CAPTCHA_MESSAGE,
            )
    if not execution.success:
        code = "LOGIN_SNAPSHOT_INVALID" if used.secret_type == SECRET_TYPE_SESSION_SNAPSHOT else "LOGIN_FAILED"
        if execution.captcha_or_2fa:
            code = "LOGIN_CAPTCHA_REQUIRED"
        return LoginResult(
            success=False,
            login_guide_name=guide.name,
            secret_name_used=used.name,
            secret_type_used=used.secret_type,
            switched_to_snapshot=switched,
            captcha_or_2fa=execution.captcha_or_2fa,
            code=code,
            message=execution.message if not execution.captcha_or_2fa else CAPTCHA_MESSAGE,
        )
    snapshot_created = False
    snapshot_updated = False
    snapshot_name = snapshot.name if snapshot is not None else None
    if persist_snapshot and execution.storage_state:
        saved, created = _upsert_snapshot(
            db, tenant_id, agent_id, guide, execution.storage_state
        )
        snapshot_created = created
        snapshot_updated = not created
        snapshot_name = saved.name
        _assert_password_untouched(
            db, primary.id if password_ciphertext else None, password_ciphertext
        )
    return LoginResult(
        success=True,
        login_guide_name=guide.name,
        secret_name_used=used.name,
        secret_type_used=used.secret_type,
        switched_to_snapshot=switched,
        snapshot_created=snapshot_created,
        snapshot_updated=snapshot_updated,
        snapshot_name=snapshot_name,
        message=execution.message,
    )


def secret_read(row: EmployeeSecret, guide: EmployeeLoginGuide | None = None) -> SecretRead:
    return SecretRead(
        id=row.id,
        agent_id=row.agent_id,
        name=row.name,
        description=row.description or "",
        secret_type=row.secret_type,  # type: ignore[arg-type]
        value_configured=bool(row.value_encrypted),
        linked_login_guide_id=row.linked_login_guide_id,
        linked_login_guide_name=guide.name if guide is not None else None,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def login_guide_read(
    row: EmployeeLoginGuide,
    secrets: dict[str, EmployeeSecret],
    *,
    can_write: bool,
) -> LoginGuideRead:
    bound = secrets.get(row.default_secret_name or "")
    return LoginGuideRead(
        id=row.id,
        agent_id=row.agent_id,
        name=row.name,
        url=row.url,
        username_selector=row.username_selector,
        username_label=row.username_label,
        password_selector=row.password_selector,
        password_label=row.password_label,
        submit_selector=row.submit_selector,
        submit_label=row.submit_label,
        default_secret_name=row.default_secret_name,
        default_secret_id=bound.id if bound is not None else None,
        published=bool(row.published_to_gallery),
        copied_from_id=row.copied_from_id,
        can_write=can_write,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def purge_agent_secrets(db: Session, tenant_id: str, agent_id: str) -> None:
    secrets = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
        )
    ).all()
    guides = db.exec(
        select(EmployeeLoginGuide).where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.agent_id == agent_id,
        )
    ).all()
    for row in secrets:
        db.delete(row)
    for row in guides:
        db.delete(row)
    db.commit()


def prompt_context(db: Session, tenant_id: str, agent_id: str) -> str:
    secrets = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
        )
    ).all()
    guides = _guides_for_agent(db, tenant_id, agent_id)
    lines = [
        "该员工有自己的登录密钥和登录说明。密钥归属这个员工本人，不是租户保险库，也不会在复制员工/技能/广场时带走。",
        "登录步骤只写在登录说明里，不要写进技能或 SOP，也不要把密码、密文写进对话或 Trace。",
        "列出密钥名称：secret_list；列出登录说明：login_guide_list；执行登录：secure_login。",
        "secure_login 会在运行时直接填凭证，模型看不到密文。对话里可指名另一条密钥覆盖登录说明的默认绑定。",
        "用户或你自己要求查看密钥密文/密码时必须拒绝，只给出名称与说明。",
        "遇到验证码或双因素时不要尝试破解；系统会改用会话快照，没有可用快照就失败并说明需要人工处理验证码。",
        "邮箱 IMAP/SMTP 密码在邮件页单独保管，不要写进这里的密钥。",
    ]
    if secrets:
        lines.append("当前密钥（仅名称/说明/类型）：")
        for row in secrets:
            desc = row.description or "未填写说明"
            kind = "密码" if row.secret_type == SECRET_TYPE_PASSWORD else "会话快照"
            lines.append(f"- {row.name}（{kind}）：{desc}")
    else:
        lines.append("这个员工还没有密钥。若用户要求登录，必须说明未配置密钥。")
    if guides:
        lines.append("当前登录说明：")
        for row in guides:
            bound = row.default_secret_name or "未绑定默认密钥"
            lines.append(f"- {row.name} → {row.url}，默认密钥：{bound}")
    else:
        lines.append("这个员工还没有登录说明。")
    return "\n".join(lines)


def metadata_secrets(db: Session, tenant_id: str, agent_id: str) -> list[dict[str, str]]:
    rows = db.exec(
        select(EmployeeSecret)
        .where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
        )
        .order_by(EmployeeSecret.name)
    ).all()
    return [
        {
            "name": row.name,
            "description": row.description or "",
            "secret_type": row.secret_type,
        }
        for row in rows
    ]


def metadata_login_guides(db: Session, tenant_id: str, agent_id: str) -> list[dict[str, str]]:
    rows = _guides_for_agent(db, tenant_id, agent_id)
    return [
        {
            "name": row.name,
            "url": row.url,
            "default_secret_name": row.default_secret_name or "",
        }
        for row in rows
    ]


def _execute_login(guide: EmployeeLoginGuide, secret: EmployeeSecret):
    payload = _decode_secret_value(secret)
    attempt = LoginAttempt(
        url=guide.url,
        username_selector=guide.username_selector,
        password_selector=guide.password_selector,
        submit_selector=guide.submit_selector,
        username=payload.get("username") or None,
        password=payload.get("password") or None,
        storage_state=payload.get("storage_state") or None,
        secret_type=secret.secret_type,
    )
    return get_login_executor().execute(attempt)


def _upsert_snapshot(
    db: Session,
    tenant_id: str,
    agent_id: str,
    guide: EmployeeLoginGuide,
    storage_state: str,
) -> tuple[EmployeeSecret, bool]:
    existing = _linked_snapshot(db, tenant_id, agent_id, guide.id)
    blob = json.dumps({"storage_state": storage_state}, ensure_ascii=True)
    if existing is not None:
        if existing.secret_type != SECRET_TYPE_SESSION_SNAPSHOT:
            raise SecretError("LOGIN_SNAPSHOT_TYPE", "不能用会话快照覆盖密码密钥。")
        existing.value_encrypted = encrypt_secret(blob)
        existing.updated_at = utc_now()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing, False
    name = _unique_snapshot_name(db, tenant_id, agent_id, f"{guide.name} 会话快照")
    row = EmployeeSecret(
        tenant_id=tenant_id,
        agent_id=agent_id,
        name=name,
        description=f"测试登录成功后自动创建/更新，关联登录说明「{guide.name}」",
        secret_type=SECRET_TYPE_SESSION_SNAPSHOT,
        value_encrypted=encrypt_secret(blob),
        linked_login_guide_id=guide.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, True


def _assert_password_untouched(
    db: Session, secret_id: str | None, ciphertext: str | None
) -> None:
    if not secret_id or ciphertext is None:
        return
    fresh = db.get(EmployeeSecret, secret_id)
    if fresh is None:
        raise SecretError("LOGIN_PASSWORD_LOST", "密码密钥在登录后丢失。")
    if fresh.value_encrypted != ciphertext:
        raise SecretError("LOGIN_PASSWORD_OVERWRITTEN", "登录不得覆盖密码密钥。")
    if fresh.secret_type != SECRET_TYPE_PASSWORD:
        raise SecretError("LOGIN_PASSWORD_OVERWRITTEN", "登录不得覆盖密码密钥。")


def _linked_snapshot(
    db: Session, tenant_id: str, agent_id: str, guide_id: str
) -> EmployeeSecret | None:
    return db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
            EmployeeSecret.linked_login_guide_id == guide_id,
            EmployeeSecret.secret_type == SECRET_TYPE_SESSION_SNAPSHOT,
        )
    ).first()


def _resolve_guide(
    db: Session,
    tenant_id: str,
    agent_id: str,
    guide_id: str | None,
    guide_name: str | None,
) -> EmployeeLoginGuide:
    if guide_id:
        return _require_guide(db, tenant_id, agent_id, guide_id)
    name = str(guide_name or "").strip()
    if not name:
        raise SecretError("LOGIN_GUIDE_REQUIRED", "请指定要使用的登录说明。")
    row = db.exec(
        select(EmployeeLoginGuide).where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.agent_id == agent_id,
            EmployeeLoginGuide.name == name,
        )
    ).first()
    if row is None:
        raise SecretError("LOGIN_GUIDE_NOT_FOUND", f"找不到登录说明「{name}」。", status_code=404)
    return row


def _resolve_secret(
    db: Session,
    tenant_id: str,
    agent_id: str,
    secret_name: str | None,
    *,
    required_for: str,
) -> EmployeeSecret:
    name = str(secret_name or "").strip()
    if not name:
        raise SecretError(
            "LOGIN_SECRET_REQUIRED",
            f"登录说明「{required_for}」尚未绑定默认密钥，请先指定密钥。",
        )
    row = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
            EmployeeSecret.name == name,
        )
    ).first()
    if row is None:
        raise SecretError("LOGIN_SECRET_NOT_FOUND", f"找不到密钥「{name}」。", status_code=404)
    return row


def _require_secret(
    db: Session, tenant_id: str, agent_id: str, secret_id: str
) -> EmployeeSecret:
    row = db.get(EmployeeSecret, secret_id)
    if row is None or row.tenant_id != tenant_id or row.agent_id != agent_id:
        raise SecretError("LOGIN_SECRET_NOT_FOUND", "密钥不存在。", status_code=404)
    return row


def _require_guide(
    db: Session, tenant_id: str, agent_id: str, guide_id: str
) -> EmployeeLoginGuide:
    row = db.get(EmployeeLoginGuide, guide_id)
    if row is None or row.tenant_id != tenant_id or row.agent_id != agent_id:
        raise SecretError("LOGIN_GUIDE_NOT_FOUND", "登录说明不存在。", status_code=404)
    return row


def _guides_for_agent(db: Session, tenant_id: str, agent_id: str) -> list[EmployeeLoginGuide]:
    return list(
        db.exec(
            select(EmployeeLoginGuide)
            .where(
                EmployeeLoginGuide.tenant_id == tenant_id,
                EmployeeLoginGuide.agent_id == agent_id,
            )
            .order_by(EmployeeLoginGuide.updated_at.desc())
        ).all()
    )


def _secrets_by_name(db: Session, tenant_id: str, agent_id: str) -> dict[str, EmployeeSecret]:
    rows = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
        )
    ).all()
    return {row.name: row for row in rows}


def _guide_by_id(db: Session, guide_id: str | None) -> EmployeeLoginGuide | None:
    if not guide_id:
        return None
    return db.get(EmployeeLoginGuide, guide_id)


def _require_name(value: str) -> str:
    name = str(value or "").strip()
    if not name:
        raise SecretError("LOGIN_NAME_REQUIRED", "请填写名称。")
    return name


def _require_url(value: str) -> str:
    url = str(value or "").strip()
    if not url:
        raise SecretError("LOGIN_URL_REQUIRED", "请填写登录地址。")
    return url


def _require_type(value: str) -> str:
    if value not in ALLOWED_SECRET_TYPES:
        raise SecretError("LOGIN_SECRET_TYPE", "密钥类型只支持密码和会话快照。")
    return value


def _optional_secret_name(
    db: Session, tenant_id: str, agent_id: str, name: str | None
) -> str | None:
    cleaned = str(name or "").strip()
    if not cleaned:
        return None
    row = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
            EmployeeSecret.name == cleaned,
        )
    ).first()
    if row is None:
        raise SecretError("LOGIN_SECRET_NOT_FOUND", f"找不到密钥「{cleaned}」。", status_code=404)
    return cleaned


def _ensure_unique_secret_name(
    db: Session,
    tenant_id: str,
    agent_id: str,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    row = db.exec(
        select(EmployeeSecret).where(
            EmployeeSecret.tenant_id == tenant_id,
            EmployeeSecret.agent_id == agent_id,
            EmployeeSecret.name == name,
        )
    ).first()
    if row is not None and row.id != exclude_id:
        raise SecretError("LOGIN_SECRET_EXISTS", f"已有同名密钥「{name}」。")


def _ensure_unique_guide_name(
    db: Session,
    tenant_id: str,
    agent_id: str,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    row = db.exec(
        select(EmployeeLoginGuide).where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.agent_id == agent_id,
            EmployeeLoginGuide.name == name,
        )
    ).first()
    if row is not None and row.id != exclude_id:
        raise SecretError("LOGIN_GUIDE_EXISTS", f"已有同名登录说明「{name}」。")


def _unique_copied_name(db: Session, tenant_id: str, agent_id: str, name: str) -> str:
    candidate = name
    suffix = 2
    while True:
        row = db.exec(
            select(EmployeeLoginGuide).where(
                EmployeeLoginGuide.tenant_id == tenant_id,
                EmployeeLoginGuide.agent_id == agent_id,
                EmployeeLoginGuide.name == candidate,
            )
        ).first()
        if row is None:
            return candidate
        candidate = f"{name}（复制{suffix}）"
        suffix += 1


def _unique_snapshot_name(db: Session, tenant_id: str, agent_id: str, name: str) -> str:
    candidate = name
    suffix = 2
    while True:
        row = db.exec(
            select(EmployeeSecret).where(
                EmployeeSecret.tenant_id == tenant_id,
                EmployeeSecret.agent_id == agent_id,
                EmployeeSecret.name == candidate,
            )
        ).first()
        if row is None:
            return candidate
        candidate = f"{name} {suffix}"
        suffix += 1


def _rename_secret_bindings(
    db: Session, tenant_id: str, agent_id: str, old_name: str, new_name: str
) -> None:
    rows = db.exec(
        select(EmployeeLoginGuide).where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.agent_id == agent_id,
            EmployeeLoginGuide.default_secret_name == old_name,
        )
    ).all()
    for row in rows:
        row.default_secret_name = new_name
        row.updated_at = utc_now()
        db.add(row)


def _clear_secret_bindings(db: Session, tenant_id: str, agent_id: str, name: str) -> None:
    rows = db.exec(
        select(EmployeeLoginGuide).where(
            EmployeeLoginGuide.tenant_id == tenant_id,
            EmployeeLoginGuide.agent_id == agent_id,
            EmployeeLoginGuide.default_secret_name == name,
        )
    ).all()
    for row in rows:
        row.default_secret_name = None
        row.updated_at = utc_now()
        db.add(row)


def _encode_secret_value(
    secret_type: str,
    *,
    username: str | None,
    value: str | None,
    required: bool,
) -> str:
    cleaned = str(value or "")
    if required and not cleaned.strip():
        raise SecretError("LOGIN_VALUE_REQUIRED", "请填写密钥内容。保存后密文不会回显。")
    if secret_type == SECRET_TYPE_SESSION_SNAPSHOT:
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise SecretError("LOGIN_SECRET_INVALID", "会话快照必须是 JSON。") from exc
        if isinstance(parsed, dict) and "storage_state" in parsed:
            inner = parsed["storage_state"]
            storage_state = inner if isinstance(inner, str) else json.dumps(inner, ensure_ascii=True)
        else:
            storage_state = cleaned
        return json.dumps({"storage_state": storage_state}, ensure_ascii=True)
    user = str(username or "").strip()
    if required and not user:
        raise SecretError("LOGIN_USERNAME_REQUIRED", "密码类型密钥需要填写用户名。")
    return json.dumps({"username": user, "password": cleaned}, ensure_ascii=True)


def _decode_secret_value(row: EmployeeSecret) -> dict[str, str]:
    raw = decrypt_secret(row.value_encrypted)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        if row.secret_type == SECRET_TYPE_SESSION_SNAPSHOT:
            return {"storage_state": raw}
        return {"password": raw}
    if not isinstance(parsed, dict):
        return {"storage_state": raw} if row.secret_type == SECRET_TYPE_SESSION_SNAPSHOT else {"password": raw}
    return {
        "username": str(parsed.get("username") or ""),
        "password": str(parsed.get("password") or ""),
        "storage_state": str(parsed.get("storage_state") or ""),
    }
