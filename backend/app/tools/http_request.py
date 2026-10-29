from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
UNREPLACED_PLACEHOLDER_MESSAGE = "URL 仍有未替换的占位符，请用英文字段名如 `{org_id}`。"
CALENDAR_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
CALENDAR_DATE_TO_UNIX = "calendar_date_to_unix"
BOUND_START = "start"
BOUND_END_EXCLUSIVE = "end_exclusive"


class UrlPlaceholderError(ValueError):
    def __init__(self, message: str, *, code: str = "URL_PLACEHOLDER") -> None:
        super().__init__(message)
        self.code = code


class RequestTransformError(ValueError):
    def __init__(self, message: str, *, code: str = "REQUEST_TRANSFORM") -> None:
        super().__init__(message)
        self.code = code


def resolve_request_transforms(
    config_json: dict[str, Any] | None,
    input_schema: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Load request transforms from tool config or input schema extensions.

    Preference order mirrors idempotency: ``config_json.request_transforms``,
    then ``input_schema["x-request-transforms"]`` / ``x_request_transforms``.
    Schema-hosted transforms survive HTTP console saves that send empty
    ``mcp_config``.
    """
    config = config_json if isinstance(config_json, dict) else {}
    schema = input_schema if isinstance(input_schema, dict) else {}
    raw = config.get("request_transforms")
    if raw is None:
        raw = schema.get("x-request-transforms", schema.get("x_request_transforms"))
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def apply_request_argument_transforms(
    arguments: dict[str, Any] | None,
    transforms: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Rewrite tool arguments before URL/query/body shaping.

    Supported transform types:
    - ``calendar_date_to_unix``: ``YYYY-MM-DD`` → unix seconds in a named TZ.
      ``bound=start`` → that day's 00:00:00; ``bound=end_exclusive`` → next
      day's 00:00:00 (gateway half-open windows). Consumes the source key.
    """
    remaining = dict(arguments or {})
    if not transforms:
        return remaining

    for index, transform in enumerate(transforms):
        transform_type = str(transform.get("type") or "").strip()
        if not transform_type:
            raise RequestTransformError(f"request_transforms[{index}] 缺少 type")
        if transform_type == CALENDAR_DATE_TO_UNIX:
            _apply_calendar_date_to_unix(remaining, transform, index=index)
            continue
        raise RequestTransformError(
            f"request_transforms[{index}] 不支持的 type：{transform_type}"
        )
    return remaining


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
    *,
    transforms: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    shaped_arguments = apply_request_argument_transforms(arguments, transforms)
    request_url, remaining = apply_url_placeholders(url, shaped_arguments)
    if (method or "GET").upper() == "GET":
        return prepare_get_request(request_url, remaining)
    return request_url, {"json": remaining}


def _apply_calendar_date_to_unix(
    arguments: dict[str, Any],
    transform: dict[str, Any],
    *,
    index: int,
) -> None:
    source = str(transform.get("source") or "").strip()
    target = str(transform.get("target") or "").strip()
    timezone_name = str(transform.get("timezone") or "Asia/Shanghai").strip() or "Asia/Shanghai"
    bound = str(transform.get("bound") or BOUND_START).strip() or BOUND_START
    if not source or not target:
        raise RequestTransformError(
            f"request_transforms[{index}] calendar_date_to_unix 需要 source 与 target"
        )
    if bound not in {BOUND_START, BOUND_END_EXCLUSIVE}:
        raise RequestTransformError(
            f"request_transforms[{index}] bound 仅支持 start 或 end_exclusive"
        )
    if source not in arguments or arguments.get(source) is None:
        return

    raw_value = arguments.pop(source)
    text = str(raw_value).strip()
    match = CALENDAR_DATE_RE.fullmatch(text)
    if not match:
        raise RequestTransformError(
            f"参数 {source} 须为 YYYY-MM-DD 日历日，收到：{text or '(空)'}"
        )
    year, month, day = (int(part) for part in match.groups())
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RequestTransformError(
            f"request_transforms[{index}] 时区无效：{timezone_name}"
        ) from exc
    try:
        day_start = datetime(year, month, day, tzinfo=zone)
    except ValueError as exc:
        raise RequestTransformError(f"参数 {source} 不是合法日历日：{text}") from exc

    instant = day_start if bound == BOUND_START else day_start + timedelta(days=1)
    arguments[target] = int(instant.timestamp())
