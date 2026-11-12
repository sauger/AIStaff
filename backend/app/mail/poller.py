from __future__ import annotations

import logging
import threading

from sqlmodel import Session

from app.db import engine
from app.mail.inbound import triage_all_pending
from app.mail.service import POLL_INTERVAL_SECONDS, sync_all_mailboxes

logger = logging.getLogger(__name__)
_stopped = False
_background_thread: threading.Thread | None = None
_wake = threading.Event()


def run_poller(*, once: bool = False, poll_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    while not _stopped:
        try:
            with Session(engine) as db:
                sync_all_mailboxes(db)
                # Routing is separate from synchronization. It lazily requests
                # text for classification and complete files only for a selected skill.
                triage_all_pending(db)
        except Exception:
            logger.exception("邮箱同步或分流失败，下个周期重试")
        if once:
            return
        _wake.wait(max(5.0, poll_seconds))
        _wake.clear()


def start_mail_poller(*, poll_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    global _background_thread, _stopped
    if _background_thread and _background_thread.is_alive():
        return
    _stopped = False
    _wake.clear()
    _background_thread = threading.Thread(
        target=run_poller,
        kwargs={"once": False, "poll_seconds": poll_seconds},
        name="rapidstaff-mail-poller",
        daemon=True,
    )
    _background_thread.start()


def stop_mail_poller() -> None:
    global _stopped
    _stopped = True
    _wake.set()
