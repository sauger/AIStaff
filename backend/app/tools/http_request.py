from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
UNREPLACED_PLACEHOLDER_MESSAGE = "URL 仍有未替换的占位符，请用英文字段名如 `{org_id}`。"


class UrlPlaceholderError(ValueError):
    def __init__(self, message: str, *, code: str = "URL_PLACEHOLDER") -> None:
        super().__init__(message)
        self.code = code


def apply_url_placeholders(
    url: str,
    arguments: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    """Replace `{name}` placeholders from arguments and return leftover args.

    `name` must match `[A-Za-z_][A-Za-z0-9_]*`. Used keys are removed from the
    returned argument map so GET leftovers can still become query params and
    POST leftovers can still become a JSON body.
    """
    remaining = dict(arguments or {})
    names = list(dict.fromkeys(PLACEHOLDER_RE.findall(url)))
    if not names:
        if "{" in url:
            raise UrlPlaceholderError(UNREPLACED_PLACEHOLDER_MESSAGE)
        return url, remaining

    missing = [name for name in names if remaining.get(name) is None]
    if missing:
        raise UrlPlaceholderError(f"URL 缺少占位符参数：{', '.join(missing)}")

    replacements: dict[str, str] = {}
    for name in names:
        text = str(remaining[name]).strip()
        if not text or "/" in text or ".." in text:
            raise UrlPlaceholderError(f"占位符 {name} 的值不合法：不能为空，也不能包含 / 或 ..")
        replacements[name] = quote(text, safe="")

    substituted = PLACEHOLDER_RE.sub(lambda match: replacements[match.group(1)], url)
    if "{" in substituted:
        raise UrlPlaceholderError(UNREPLACED_PLACEHOLDER_MESSAGE)

    for name in names:
        remaining.pop(name, None)
    return substituted, remaining


def prepare_get_request(url: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    clean_params = {str(key): value for key, value in (params or {}).items() if value is not None}
    if not clean_params:
        return url, {}

    parsed = urlsplit(url)
    if not parsed.query:
        return url, {"params": clean_params}

    merged_params: dict[str, Any] = dict(parse_qsl(parsed.query, keep_blank_values=True))
    merged_params.update(clean_params)
    merged_url = urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(merged_params, doseq=True),
            parsed.fragment,
        )
    )
    return merged_url, {}


def prepare_http_request(
    method: str,
    url: str,
    arguments: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    request_url, remaining = apply_url_placeholders(url, arguments)
    if (method or "GET").upper() == "GET":
        return prepare_get_request(request_url, remaining)
    return request_url, {"json": remaining}
