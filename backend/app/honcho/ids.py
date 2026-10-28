"""Map StaffDeck identities onto Honcho resource names.

Logical ids follow the design: ``tenant:{id}``, ``user:{User.id}``,
``agent:{agent_id}``, ``chat:{ChatSession.id}``. Honcho resource names only
allow ``[A-Za-z0-9_-]`` and reject ``:``, so the wire id replaces that single
separator with ``_``. A raw id that already contains ``:`` is rejected instead
of being folded into another peer. Channel external ids never enter the wire id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WIRE_ID = re.compile(r"^[A-Za-z0-9_-]{1,100}$")


class HonchoIdError(ValueError):
    """The logical id cannot be sent to Honcho without collapsing identities."""


@dataclass(frozen=True)
class HonchoIdentity:
    workspace_logical: str
    workspace_id: str
    user_peer_logical: str
    user_peer_id: str
    agent_peer_logical: str
    agent_peer_id: str
    session_logical: str
    session_id: str
    user_peer_metadata: dict[str, str]
    agent_peer_metadata: dict[str, str]
    session_metadata: dict[str, str]


def wire_id(logical: str) -> str:
    if logical.count(":") != 1:
        raise HonchoIdError(logical)
    kind, raw = logical.split(":", 1)
    if not kind or not raw:
        raise HonchoIdError(logical)
    wire = f"{kind}_{raw}"
    if _WIRE_ID.fullmatch(wire) is None:
        raise HonchoIdError(logical)
    return wire


def build_honcho_identity(
    *,
    tenant_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    workspace_prefix: str = "tenant:",
    channel: str | None = None,
    account_scope: str | None = None,
    external_user_id: str | None = None,
) -> HonchoIdentity:
    workspace_logical = f"{workspace_prefix}{tenant_id}"
    user_peer_logical = f"user:{user_id}"
    agent_peer_logical = f"agent:{agent_id}"
    session_logical = f"chat:{session_id}"
    channel_metadata = _channel_metadata(
        channel=channel,
        account_scope=account_scope,
        external_user_id=external_user_id,
    )
    return HonchoIdentity(
        workspace_logical=workspace_logical,
        workspace_id=wire_id(workspace_logical),
        user_peer_logical=user_peer_logical,
        user_peer_id=wire_id(user_peer_logical),
        agent_peer_logical=agent_peer_logical,
        agent_peer_id=wire_id(agent_peer_logical),
        session_logical=session_logical,
        session_id=wire_id(session_logical),
        user_peer_metadata={"staff_peer": user_peer_logical, **channel_metadata},
        agent_peer_metadata={"staff_peer": agent_peer_logical},
        session_metadata={"staff_session": session_logical, **channel_metadata},
    )


def _channel_metadata(
    *,
    channel: str | None,
    account_scope: str | None,
    external_user_id: str | None,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    if channel:
        metadata["channel"] = channel
    if account_scope:
        metadata["account_scope"] = account_scope
    if external_user_id:
        metadata["external_user_id"] = external_user_id
    return metadata
