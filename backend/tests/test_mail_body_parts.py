import base64

import pytest

from app.mail import transport
from app.mail.body_parts import _fetch_structure, _text_sections, fetch_text_sections
from app.mail.errors import MailError
from app.mail.transport import MailboxConnection, StdlibMailTransport

PLAIN = '("TEXT" "PLAIN" ("CHARSET" "utf-8") NIL NIL "BASE64" 40 1 NIL NIL NIL NIL)'
HTML = '("TEXT" "HTML" ("CHARSET" "utf-8") NIL NIL "QUOTED-PRINTABLE" 100 1 NIL NIL NIL NIL)'
ATTACHMENT = '("APPLICATION" "PDF" ("NAME" "big.pdf") NIL NIL "BASE64" 20000000 NIL ("ATTACHMENT" ("FILENAME" "big.pdf")) NIL NIL)'
TEXT_ATTACHMENT = '("TEXT" "PLAIN" ("NAME" "private.txt") NIL NIL "BASE64" 100 1 NIL ("ATTACHMENT" ("FILENAME" "private.txt")) NIL NIL)'


class BodyIMAP:
    def __init__(self, structure, bodies):
        self.structure, self.bodies = structure, bodies
        self.calls = []
        self.readonly = None

    def login(self, *args):
        return "OK", []

    def select(self, name, readonly=False):
        self.readonly = readonly
        return "OK", []

    def logout(self):
        return "BYE", []

    def uid(self, command, uid, query):
        self.calls.append(query)
        assert command == "FETCH"
        if "HEADER.FIELDS" in query:
            return "OK", [(b'1 (UID 77 FLAGS () BODY[HEADER.FIELDS] {100}',
                            b'Subject: bill\r\nFrom: sender@example.com\r\nMessage-ID: <77@example.com>\r\n\r\n')]
        if query == "(BODYSTRUCTURE)":
            return "OK", [("1 (UID 77 BODYSTRUCTURE " + self.structure + ")").encode()]
        assert all(part in query for part in [f"BODY.PEEK[{key}]" for key in self.bodies])
        return "OK", [(f"1 (UID 77 BODY[{key}] {{{len(value)}}}".encode(), value)
                      for key, value in self.bodies.items()]


def test_body_fetch_reads_only_plain_mime_sections_not_attachments(monkeypatch):
    structure = f'(({PLAIN}{HTML} "ALTERNATIVE" NIL NIL NIL) {TEXT_ATTACHMENT} {ATTACHMENT} "MIXED" NIL NIL NIL)'
    client = BodyIMAP(structure, {"1.1": base64.b64encode("正文金额 3200 元".encode())})
    monkeypatch.setattr(transport, "_imap_connect", lambda mailbox: client)
    mailbox = MailboxConnection("a@b.com", "a", "secret", "imap", 993, "ssl", "smtp", 465, "ssl")
    message = StdlibMailTransport().fetch_body(mailbox, "77")
    assert message.body_text == "正文金额 3200 元"
    assert message.attachments == []
    assert client.readonly is True
    assert client.calls[-1] == "(BODY.PEEK[1.1])"
    assert not any("RFC822" in query or "BODY.PEEK[]" in query for query in client.calls)


def test_html_only_body_is_decoded_and_attachment_only_mail_is_valid():
    client = BodyIMAP(f'({HTML} {ATTACHMENT} "MIXED" NIL NIL NIL)',
                      {"1": b'<p>Hello =E4=B8=96=E7=95=8C</p><script>hidden</script>'})
    assert fetch_text_sections(client, "77") == "Hello 世界"
    no_text = BodyIMAP(ATTACHMENT, {})
    assert fetch_text_sections(no_text, "77") == ""
    assert no_text.calls == ["(BODYSTRUCTURE)"]


def test_structure_literals_and_attachment_names_are_not_treated_as_body():
    literal = b'private name.txt'
    prefix = b'1 (BODYSTRUCTURE ("TEXT" "PLAIN" ("NAME" {' + str(len(literal)).encode() + b'}'
    parsed = _fetch_structure([(prefix, literal), b') NIL NIL "7BIT" 10 1 NIL NIL NIL NIL))'])
    assert _text_sections(parsed) == []


def test_invalid_or_missing_body_sections_fail_without_fetching_full_mime():
    with pytest.raises(MailError):
        _fetch_structure([b'1 (BODYSTRUCTURE (("TEXT"'])
    client = BodyIMAP(PLAIN, {})
    with pytest.raises(MailError, match="返回不完整"):
        fetch_text_sections(client, "77")
    assert client.calls == ["(BODYSTRUCTURE)", "(BODY.PEEK[1])"]
