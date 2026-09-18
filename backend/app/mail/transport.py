from __future__ import annotations

import imaplib
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, getaddresses, make_msgid, parsedate_to_datetime
from typing import Protocol

from app.mail.errors import MailError

ENCRYPTION_MODES = {"ssl", "starttls", "none"}


@dataclass
class FetchedAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class FetchedMessage:
    uid: str
    rfc_message_id: str
    from_address: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    body_text: str
    date: datetime | None
    unseen: bool
    attachments: list[FetchedAttachment] = field(default_factory=list)


@dataclass
class OutboundAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass
class MailboxConnection:
    email_address: str
    username: str
    password: str
    imap_host: str
    imap_port: int
    imap_encryption: str
    smtp_host: str
    smtp_port: int
    smtp_encryption: str


class MailTransport(Protocol):
    def probe_imap(self, mailbox: MailboxConnection) -> None: ...

    def probe_smtp(self, mailbox: MailboxConnection) -> None: ...

    def fetch_inbox(self, mailbox: MailboxConnection) -> list[FetchedMessage]: ...

    def mark_seen(self, mailbox: MailboxConnection, uid: str) -> None: ...

    def send(
        self,
        mailbox: MailboxConnection,
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        attachments: list[OutboundAttachment],
        in_reply_to: str | None = None,
    ) -> tuple[str, str | None]: ...

    def append_sent(self, mailbox: MailboxConnection, raw: bytes) -> None: ...


_transport: MailTransport | None = None


def get_transport() -> MailTransport:
    return _transport or StdlibMailTransport()


def set_transport(transport: MailTransport | None) -> None:
    global _transport
    _transport = transport


def validate_encryption(value: str, *, field: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in ENCRYPTION_MODES:
        raise MailError(
            "MAIL_ENCRYPTION_INVALID",
            f"{field} 加密方式无效，请使用 ssl、starttls 或 none。",
            details={"field": field},
        )
    return mode


class StdlibMailTransport:
    def probe_imap(self, mailbox: MailboxConnection) -> None:
        client = None
        try:
            client = _imap_connect(mailbox)
            status, _ = client.login(mailbox.username, mailbox.password)
            if status != "OK":
                raise MailError(
                    "MAIL_IMAP_AUTH_FAILED",
                    f"IMAP 认证失败（主机 {mailbox.imap_host}）。请检查用户名和密码。",
                    details={"host": mailbox.imap_host, "port": mailbox.imap_port},
                )
        except MailError:
            raise
        except Exception as exc:
            raise _imap_error(mailbox, exc) from exc
        finally:
            _imap_logout(client)

    def probe_smtp(self, mailbox: MailboxConnection) -> None:
        client = None
        try:
            client = _smtp_connect(mailbox)
            client.login(mailbox.username, mailbox.password)
        except MailError:
            raise
        except Exception as exc:
            raise _smtp_error(mailbox, exc) from exc
        finally:
            _smtp_quit(client)

    def fetch_inbox(self, mailbox: MailboxConnection) -> list[FetchedMessage]:
        client = None
        try:
            client = _imap_connect(mailbox)
            status, _ = client.login(mailbox.username, mailbox.password)
            if status != "OK":
                raise MailError(
                    "MAIL_IMAP_AUTH_FAILED",
                    f"IMAP 认证失败（主机 {mailbox.imap_host}）。请检查用户名和密码。",
                    details={"host": mailbox.imap_host},
                )
            selected, _data = client.select("INBOX", readonly=True)
            if selected != "OK":
                raise MailError(
                    "MAIL_IMAP_INBOX_FAILED",
                    f"无法打开 IMAP INBOX（主机 {mailbox.imap_host}）。",
                    details={"host": mailbox.imap_host},
                )
            status, payload = client.uid("SEARCH", None, "ALL")
            if status != "OK":
                raise MailError(
                    "MAIL_IMAP_SEARCH_FAILED",
                    f"无法列出 INBOX（主机 {mailbox.imap_host}）。",
                    details={"host": mailbox.imap_host},
                )
            uids = (payload[0] or b"").split()
            messages: list[FetchedMessage] = []
            for uid_bytes in uids:
                uid = uid_bytes.decode("ascii", errors="ignore")
                fetched = _fetch_one(client, uid)
                if fetched is not None:
                    messages.append(fetched)
            return messages
        except MailError:
            raise
        except Exception as exc:
            raise _imap_error(mailbox, exc) from exc
        finally:
            _imap_logout(client)

    def mark_seen(self, mailbox: MailboxConnection, uid: str) -> None:
        if not uid:
            return
        client = None
        try:
            client = _imap_connect(mailbox)
            client.login(mailbox.username, mailbox.password)
            client.select("INBOX")
            client.uid("STORE", uid, "+FLAGS", "(\\Seen)")
        except Exception:  # noqa: BLE001 - inbox open already succeeded locally.
            return
        finally:
            _imap_logout(client)

    def send(
        self,
        mailbox: MailboxConnection,
        *,
        to: list[str],
        cc: list[str],
        bcc: list[str],
        subject: str,
        body: str,
        attachments: list[OutboundAttachment],
        in_reply_to: str | None = None,
    ) -> tuple[str, str | None]:
        message = EmailMessage()
        message["From"] = mailbox.email_address
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        message["Subject"] = subject
        message["Date"] = formatdate(localtime=True)
        rfc_id = make_msgid()
        message["Message-ID"] = rfc_id
        if in_reply_to:
            message["In-Reply-To"] = in_reply_to
        message.set_content(body or "")
        for item in attachments:
            maintype, _, subtype = (item.content_type or "application/octet-stream").partition("/")
            message.add_attachment(
                item.data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=item.filename,
            )
        recipients = list(dict.fromkeys([*to, *cc, *bcc]))
        client = None
        try:
            client = _smtp_connect(mailbox)
            client.login(mailbox.username, mailbox.password)
            client.send_message(message, from_addr=mailbox.email_address, to_addrs=recipients)
        except MailError:
            raise
        except Exception as exc:
            raise _smtp_error(mailbox, exc) from exc
        finally:
            _smtp_quit(client)
        raw = message.as_bytes()
        try:
            self.append_sent(mailbox, raw)
        except MailError as exc:
            return rfc_id, exc.message
        except Exception:  # noqa: BLE001 - D2: APPEND failure must not fail SMTP send.
            return rfc_id, (
                f"SMTP 已投递，但未能写入服务器已发送文件夹（主机 {mailbox.imap_host}）。"
            )
        return rfc_id, None

    def append_sent(self, mailbox: MailboxConnection, raw: bytes) -> None:
        client = None
        try:
            client = _imap_connect(mailbox)
            client.login(mailbox.username, mailbox.password)
            for name in ("Sent", "INBOX.Sent", "Sent Messages"):
                status, _ = client.append(name, "\\Seen", None, raw)
                if status == "OK":
                    return
            raise MailError(
                "MAIL_SENT_APPEND_FAILED",
                f"SMTP 已投递，但未能写入服务器已发送文件夹（主机 {mailbox.imap_host}）。",
                details={"host": mailbox.imap_host, "delivered": True},
            )
        except MailError:
            raise
        except Exception as exc:
            raise MailError(
                "MAIL_SENT_APPEND_FAILED",
                f"SMTP 已投递，但未能写入服务器已发送文件夹（主机 {mailbox.imap_host}）。",
                details={"host": mailbox.imap_host, "delivered": True},
            ) from exc
        finally:
            _imap_logout(client)


def parse_rfc822(uid: str, raw: bytes, *, unseen: bool) -> FetchedMessage:
    from email import policy
    from email.parser import BytesParser

    parsed = BytesParser(policy=policy.default).parsebytes(raw)
    from_address = _first_address(parsed.get("From"))
    return FetchedMessage(
        uid=uid,
        rfc_message_id=str(parsed.get("Message-ID") or "").strip(),
        from_address=from_address,
        to=_address_list(parsed.get("To")),
        cc=_address_list(parsed.get("Cc")),
        bcc=_address_list(parsed.get("Bcc")),
        subject=str(parsed.get("Subject") or "").strip(),
        body_text=extract_body_text(parsed),
        date=_parse_date(parsed.get("Date")),
        unseen=unseen,
        attachments=_collect_attachments(parsed),
    )


def extract_body_text(message: EmailMessage) -> str:
    if message.is_multipart():
        plain_parts: list[str] = []
        html_parts: list[str] = []
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type == "text/plain":
                plain_parts.append(_decode_part_text(part))
            elif content_type == "text/html":
                html_parts.append(_decode_part_text(part))
        if plain_parts:
            return "\n\n".join(item for item in plain_parts if item).strip()
        return html_to_text("\n".join(html_parts))
    content_type = message.get_content_type()
    text = _decode_part_text(message)
    if content_type == "text/html":
        return html_to_text(text)
    return text.strip()


def html_to_text(value: str) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(value or "", "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _fetch_one(client: imaplib.IMAP4, uid: str) -> FetchedMessage | None:
    status, payload = client.uid("FETCH", uid, "(FLAGS RFC822)")
    if status != "OK" or not payload or payload[0] is None:
        return None
    meta, raw = _split_fetch(payload[0])
    if not raw:
        return None
    unseen = b"\\Seen" not in meta
    return parse_rfc822(uid, raw, unseen=unseen)


def _split_fetch(item: object) -> tuple[bytes, bytes]:
    if isinstance(item, tuple) and len(item) >= 2:
        return bytes(item[0]), bytes(item[1])
    if isinstance(item, bytes):
        return item, b""
    return b"", b""


def _collect_attachments(message: EmailMessage) -> list[FetchedAttachment]:
    attachments: list[FetchedAttachment] = []
    if not message.is_multipart():
        return attachments
    for part in message.walk():
        disposition = str(part.get_content_disposition() or "")
        filename = part.get_filename()
        if disposition != "attachment" and not filename:
            continue
        if not filename:
            filename = "attachment"
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            FetchedAttachment(
                filename=str(filename),
                content_type=part.get_content_type() or "application/octet-stream",
                data=payload,
            )
        )
    return attachments


def _decode_part_text(part: EmailMessage) -> str:
    try:
        payload = part.get_payload(decode=True)
    except (TypeError, ValueError, LookupError, AttributeError):
        payload = None
    if payload is None:
        return str(part.get_payload() or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _first_address(value: object) -> str:
    addresses = _address_list(value)
    return addresses[0] if addresses else str(value or "").strip()


def _address_list(value: object) -> list[str]:
    if not value:
        return []
    return [address for _name, address in getaddresses([str(value)]) if address]


def _parse_date(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is not None:
        return parsed.replace(tzinfo=None)
    return parsed


def _imap_connect(mailbox: MailboxConnection) -> imaplib.IMAP4:
    timeout = 20
    if mailbox.imap_encryption == "ssl":
        return imaplib.IMAP4_SSL(mailbox.imap_host, mailbox.imap_port, timeout=timeout)
    client = imaplib.IMAP4(mailbox.imap_host, mailbox.imap_port, timeout=timeout)
    if mailbox.imap_encryption == "starttls":
        context = ssl.create_default_context()
        client.starttls(ssl_context=context)
    return client


def _smtp_connect(mailbox: MailboxConnection) -> smtplib.SMTP:
    timeout = 20
    if mailbox.smtp_encryption == "ssl":
        client = smtplib.SMTP_SSL(mailbox.smtp_host, mailbox.smtp_port, timeout=timeout)
        client.ehlo()
        return client
    client = smtplib.SMTP(mailbox.smtp_host, mailbox.smtp_port, timeout=timeout)
    client.ehlo()
    if mailbox.smtp_encryption == "starttls":
        context = ssl.create_default_context()
        client.starttls(context=context)
        client.ehlo()
    return client


def _imap_logout(client: imaplib.IMAP4 | None) -> None:
    if client is None:
        return
    try:
        client.logout()
    except (OSError, imaplib.IMAP4.error):
        try:
            client.shutdown()
        except OSError:
            return


def _smtp_quit(client: smtplib.SMTP | None) -> None:
    if client is None:
        return
    try:
        client.quit()
    except (OSError, smtplib.SMTPException):
        try:
            client.close()
        except OSError:
            return


def _imap_error(mailbox: MailboxConnection, exc: Exception) -> MailError:
    if isinstance(exc, imaplib.IMAP4.error) and "auth" in str(exc).lower():
        return MailError(
            "MAIL_IMAP_AUTH_FAILED",
            f"IMAP 认证失败（主机 {mailbox.imap_host}）。请检查用户名和密码。",
            details={"host": mailbox.imap_host, "port": mailbox.imap_port},
        )
    return MailError(
        "MAIL_IMAP_FAILED",
        f"IMAP 连接失败（主机 {mailbox.imap_host}:{mailbox.imap_port}）：{_safe_exc(exc)}",
        details={"host": mailbox.imap_host, "port": mailbox.imap_port},
    )


def _smtp_error(mailbox: MailboxConnection, exc: Exception) -> MailError:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError(
            "MAIL_SMTP_AUTH_FAILED",
            f"SMTP 认证失败（主机 {mailbox.smtp_host}）。请检查用户名和密码。",
            details={"host": mailbox.smtp_host, "port": mailbox.smtp_port},
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return MailError(
            "MAIL_RECIPIENT_REJECTED",
            f"SMTP 拒绝了收件人（主机 {mailbox.smtp_host}）。",
            details={"host": mailbox.smtp_host},
        )
    return MailError(
        "MAIL_SMTP_FAILED",
        f"SMTP 连接或发送失败（主机 {mailbox.smtp_host}:{mailbox.smtp_port}）：{_safe_exc(exc)}",
        details={"host": mailbox.smtp_host, "port": mailbox.smtp_port},
    )


def _safe_exc(exc: Exception) -> str:
    text = str(exc or exc.__class__.__name__).replace("\n", " ").strip()
    return text[:180] or exc.__class__.__name__
