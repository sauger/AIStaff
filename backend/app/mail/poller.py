from __future__ import annotations

import threading
from time import sleep

from sqlmodel import Session

from app.db import engine
from app.mail.inbound import triage_all_pending
from app.mail.service import POLL_INTERVAL_SECONDS, sync_all_mailboxes

_stopped = False
_background_thread: threading.Thread | None = None


def run_poller(*, once: bool = False, poll_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    while not _stopped:
        with Session(engine) as db:
            sync_all_mailboxes(db)
            triage_all_pending(db)
        if once:
            return
        sleep(max(5.0, poll_seconds))


def start_mail_poller(*, poll_seconds: float = POLL_INTERVAL_SECONDS) -> None:
    global _background_thread, _stopped
    if _background_thread and _background_thread.is_alive():
        return
    _stopped = False
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
