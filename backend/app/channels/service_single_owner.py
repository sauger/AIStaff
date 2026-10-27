from __future__ import annotations

import logging
from collections import defaultdict

from sqlmodel import Session, select

from app.db.models import ChannelBinding, ChannelBindingAgent

logger = logging.getLogger(__name__)


def prune_multi_agent_mounts(db: Session) -> int:
    """Keep at most one mount per binding: the row matching binding.agent_id.

    Unexpected multi-mount rows (local/prod almost never) are deleted.
    If no row matches agent_id, keep the is_default row (or first) and
    sync binding.agent_id to that row — then delete the rest.
    Team bindings (team_id set) are skipped: they do not use employee mounts.
    Returns number of mount rows deleted.
    """
    bindings = db.exec(select(ChannelBinding)).all()
    mounts = db.exec(select(ChannelBindingAgent)).all()
    by_binding: dict[str, list[ChannelBindingAgent]] = defaultdict(list)
    for row in mounts:
        by_binding[row.binding_id].append(row)

    deleted = 0
    for binding in bindings:
        if binding.team_id:
            continue
        rows = by_binding.get(binding.id) or []
        if len(rows) <= 1:
            continue
        keep = next((r for r in rows if r.agent_id == binding.agent_id), None)
        if keep is None:
            keep = next((r for r in rows if r.is_default), rows[0])
            binding.agent_id = keep.agent_id
            db.add(binding)
        for row in rows:
            if row.id == keep.id:
                continue
            db.delete(row)
            deleted += 1
        keep.is_default = True
        keep.sort_order = 0
        db.add(keep)
        logger.info(
            "pruned multi-mount binding_id=%s kept_agent_id=%s deleted=%s",
            binding.id,
            keep.agent_id,
            len(rows) - 1,
        )
    return deleted
