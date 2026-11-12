"""Fetch MIME text sections without downloading attachment payloads."""

from __future__ import annotations

import base64
import binascii
import quopri
import re

from app.mail.errors import MailError


def _fetch_structure(payload: list) -> list:
    chunks = []
    for part in payload or []:
        if isinstance(part, tuple):
            chunks.append(part[0] + b"\r\n" + part[1])
        elif isinstance(part, bytes):
            chunks.append(part)
    raw = b" ".join(chunks)
    match = re.search(rb"\bBODYSTRUCTURE\s+", raw, re.IGNORECASE)
    if not match or len(raw) > 1024 * 1024:
        raise MailError("MAIL_BODY_STRUCTURE_INVALID", "无法读取邮件 MIME 结构。")
    cursor = match.end()

    def parse(depth=0):
        nonlocal cursor
        if depth > 50:
            raise ValueError("MIME structure nesting limit")
        while cursor < len(raw) and raw[cursor : cursor + 1].isspace():
            cursor += 1
        if cursor >= len(raw):
            raise ValueError("Incomplete MIME structure")
        char = raw[cursor : cursor + 1]
        cursor += 1
        if char == b"(":
            values = []
            while True:
                while cursor < len(raw) and raw[cursor : cursor + 1].isspace():
                    cursor += 1
                if cursor >= len(raw):
                    raise ValueError("Unclosed MIME structure")
                if raw[cursor : cursor + 1] == b")":
                    cursor += 1
                    return values
                values.append(parse(depth + 1))
        if char == b'"':
            value = bytearray()
            while cursor < len(raw):
                char = raw[cursor : cursor + 1]
                cursor += 1
                if char == b'"':
                    return value.decode("utf-8", errors="replace")
                if char == b"\\":
                    if cursor >= len(raw):
                        raise ValueError("Incomplete escaped string")
                    char = raw[cursor : cursor + 1]
                    cursor += 1
                value.extend(char)
            raise ValueError("Unclosed quoted string")
        cursor -= 1
        literal = re.match(rb"\{(\d+)\+?\}\r?\n", raw[cursor:])
        if literal:
            cursor += literal.end()
            length = int(literal.group(1))
            value = raw[cursor : cursor + length]
            if len(value) != length:
                raise ValueError("Incomplete literal")
            cursor += length
            return value.decode("utf-8", errors="replace")
        atom = re.match(rb"[^\s()]+", raw[cursor:])
        if not atom:
            raise ValueError("Invalid MIME structure token")
        cursor += atom.end()
        value = atom.group().decode("ascii", errors="replace")
        return None if value.upper() == "NIL" else value

    try:
        structure = parse()
        if not isinstance(structure, list):
            raise TypeError("MIME structure is not a list")
        return structure
    except (TypeError, ValueError, IndexError) as exc:
        raise MailError(
            "MAIL_BODY_STRUCTURE_INVALID", "邮件 MIME 结构不完整，未加载正文。"
        ) from exc


def _text_sections(structure: list, prefix: str = "") -> list[tuple]:
    if not structure:
        return []
    multipart = isinstance(structure[0], list)
    if multipart:
        child_count = next(
            (i for i, value in enumerate(structure) if not isinstance(value, list)), len(structure)
        )
        params = structure[child_count + 1] if len(structure) > child_count + 1 else None
        disposition = structure[child_count + 2] if len(structure) > child_count + 2 else None
    else:
        if len(structure) < 7:
            raise MailError("MAIL_BODY_STRUCTURE_INVALID", "邮件 MIME 分段不完整。")
        params = structure[2]
        major = str(structure[0]).upper()
        disposition_index = 9 if major == "TEXT" else 11 if major == "MESSAGE" else 8
        disposition = structure[disposition_index] if len(structure) > disposition_index else None
    parameter_map = (
        dict(zip((params or [])[::2], (params or [])[1::2])) if isinstance(params, list) else {}
    )
    if (
        any(str(key).upper() in {"NAME", "FILENAME"} for key in parameter_map)
        or isinstance(disposition, list)
        and disposition
        and (
            str(disposition[0]).upper() == "ATTACHMENT"
            or isinstance(disposition[-1], list)
            and any(str(key).upper() == "FILENAME" for key in disposition[-1][::2])
        )
    ):
        return []
    if multipart:
        parts = []
        for index, child in enumerate(structure[:child_count], 1):
            parts.extend(_text_sections(child, f"{prefix}.{index}" if prefix else str(index)))
        return parts
    subtype = str(structure[1]).upper()
    if (
        major == "MESSAGE"
        and subtype == "RFC822"
        and len(structure) > 8
        and isinstance(structure[8], list)
    ):
        return _text_sections(structure[8], prefix or "1")
    if major != "TEXT" or subtype not in {"PLAIN", "HTML"}:
        return []
    charset = next(
        (str(value) for key, value in parameter_map.items() if str(key).upper() == "CHARSET"),
        "utf-8",
    )
    return [(prefix or "1", subtype, charset, str(structure[5]).upper())]


def fetch_text_sections(client, uid: str) -> str:
    status, payload = client.uid("FETCH", uid, "(BODYSTRUCTURE)")
    if status != "OK":
        raise MailError("MAIL_BODY_FETCH_FAILED", "无法读取邮件正文结构。")
    sections = _text_sections(_fetch_structure(payload))
    plain = [section for section in sections if section[1] == "PLAIN"]
    selected = plain or [section for section in sections if section[1] == "HTML"]
    if not selected:
        return ""
    query = "(" + " ".join(f"BODY.PEEK[{section[0]}]" for section in selected) + ")"
    status, payload = client.uid("FETCH", uid, query)
    if status != "OK":
        raise MailError("MAIL_BODY_FETCH_FAILED", "无法读取邮件正文分段。")
    bodies = {}
    for part in payload or []:
        if isinstance(part, tuple) and len(part) >= 2:
            match = re.search(rb"\bBODY\[([^\]]+)\]", part[0], re.IGNORECASE)
            if match:
                bodies[match.group(1).decode("ascii")] = part[1]
    text = []
    for section, subtype, charset, encoding in selected:
        if section not in bodies:
            raise MailError("MAIL_BODY_FETCH_FAILED", "正文分段返回不完整。")
        data = bodies[section]
        try:
            if encoding == "BASE64":
                data = base64.b64decode(data)
            elif encoding == "QUOTED-PRINTABLE":
                data = quopri.decodestring(data)
        except (ValueError, binascii.Error) as exc:
            raise MailError("MAIL_BODY_FETCH_FAILED", "邮件正文编码无效。") from exc
        try:
            decoded = data.decode(charset, errors="replace")
        except LookupError:
            decoded = data.decode("utf-8", errors="replace")
        if subtype == "HTML":
            from app.mail.transport import html_to_text

            decoded = html_to_text(decoded)
        text.append(decoded)
    return "\n\n".join(text).strip()
