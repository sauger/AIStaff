from email.header import Header

import pytest

from app.mail import transport
from app.mail.transport import MailboxConnection, StdlibMailTransport


class IMAP:
    def __init__(self, reject_charset=False):
        self.calls = []
        self.reject_charset = reject_charset

    def login(self, *args):
        return "OK", []

    def select(self, *args, **kwargs):
        return "OK", []

    def logout(self):
        return "BYE", []

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            if self.reject_charset and args[0] == "CHARSET":
                return "BAD", [b"unsupported charset"]
            return "OK", [b"10 20"]
        assert command == "FETCH"
        assert "BODY.PEEK[HEADER.FIELDS" in args[1]
        output = []
        for uid in args[0].split(b","):
            subject = "账单" if uid == b"10" else "无关"
            raw = ("Subject: " + Header(subject, "utf-8").encode() + "\r\n"
                   "From: sender@example.com\r\nMessage-ID: <" + uid.decode() + ">@example.com>\r\n\r\n").encode()
            output.append((b"1 (UID " + uid + b" FLAGS () BODY[HEADER.FIELDS] {100}", raw))
        return "OK", output


@pytest.mark.parametrize("fallback", [False, True])
def test_unicode_search_fetches_batched_headers_and_never_bodies(monkeypatch, fallback):
    imap = IMAP(reject_charset=fallback)
    monkeypatch.setattr(transport, "_imap_connect", lambda mailbox: imap)
    mailbox = MailboxConnection("a@b.com", "a", "secret", "imap", 993, "ssl", "smtp", 465, "ssl")
    result = StdlibMailTransport().fetch_headers(mailbox, subject="账单")
    assert result.total == 1
    assert all(item.headers_only and item.attachments == [] and item.body_text == ""
               for item in result.messages)
    fetches = [args for command, args in imap.calls if command == "FETCH"]
    assert len(fetches) == 1
    assert all("RFC822" not in args[1] and "BODY.PEEK[]" not in args[1] for args in fetches)
    # A server may return OK but ignore SUBJECT; both paths must filter correctly.
    assert [m.subject for m in result.messages] == ["账单"]
