from urllib.parse import quote

import pytest

from app.tools.http_request import (
    UNREPLACED_PLACEHOLDER_MESSAGE,
    RequestTransformError,
    UrlPlaceholderError,
    apply_request_argument_transforms,
    apply_url_placeholders,
    prepare_get_request,
    prepare_http_request,
    resolve_request_transforms,
)

EXPORT_DATE_TRANSFORMS = [
    {
        "type": "calendar_date_to_unix",
        "source": "date_from",
        "target": "from",
        "timezone": "Asia/Shanghai",
        "bound": "start",
    },
    {
        "type": "calendar_date_to_unix",
        "source": "date_to",
        "target": "to",
        "timezone": "Asia/Shanghai",
        "bound": "end_exclusive",
    },
]

BILL_URL = "https://gateway.example/api/platform/organizations/{org_id}/consumption-bills"
BILL_ARGS = {"org_id": "42", "period_ym": "2026-09", "page": 1}


def test_apply_url_placeholders_substitutes_and_removes_used_keys() -> None:
    url, remaining = apply_url_placeholders(BILL_URL, BILL_ARGS)

    assert url == "https://gateway.example/api/platform/organizations/42/consumption-bills"
    assert remaining == {"period_ym": "2026-09", "page": 1}


def test_apply_url_placeholders_encodes_path_segments() -> None:
    url, remaining = apply_url_placeholders(
        "https://gateway.example/items/{item_id}",
        {"item_id": "hello world", "keep": True},
    )

    assert url == f"https://gateway.example/items/{quote('hello world', safe='')}"
    assert remaining == {"keep": True}


def test_apply_url_placeholders_missing_key_lists_names() -> None:
    with pytest.raises(UrlPlaceholderError, match="URL 缺少占位符参数：org_id") as exc_info:
        apply_url_placeholders(BILL_URL, {"period_ym": "2026-09"})

    assert exc_info.value.code == "URL_PLACEHOLDER"


def test_apply_url_placeholders_missing_multiple_keys_preserves_order() -> None:
    with pytest.raises(UrlPlaceholderError, match="URL 缺少占位符参数：org_id, page"):
        apply_url_placeholders(
            "https://gateway.example/{org_id}/bills/{page}",
            {"period_ym": "2026-09"},
        )


def test_apply_url_placeholders_rejects_none_as_missing() -> None:
    with pytest.raises(UrlPlaceholderError, match="URL 缺少占位符参数：org_id"):
        apply_url_placeholders(BILL_URL, {"org_id": None, "period_ym": "2026-09"})


@pytest.mark.parametrize("value", ["", "  ", "/", "org/42", "..", "../42", "a..b"])
def test_apply_url_placeholders_rejects_traversal_or_empty(value: str) -> None:
    with pytest.raises(UrlPlaceholderError, match="占位符 org_id 的值不合法"):
        apply_url_placeholders(BILL_URL, {"org_id": value})


def test_apply_url_placeholders_rejects_leftover_non_ascii_braces() -> None:
    with pytest.raises(UrlPlaceholderError, match=UNREPLACED_PLACEHOLDER_MESSAGE):
        apply_url_placeholders(
            "https://gateway.example/api/platform/organizations/{企业数字ID}/consumption-bills",
            {"企业数字ID": "42", "period_ym": "2026-09"},
        )


def test_apply_url_placeholders_rejects_mixed_ascii_and_leftover_braces() -> None:
    with pytest.raises(UrlPlaceholderError, match=UNREPLACED_PLACEHOLDER_MESSAGE):
        apply_url_placeholders(
            "https://gateway.example/{org_id}/{企业数字ID}/bills",
            {"org_id": "42"},
        )


def test_apply_url_placeholders_leaves_plain_url_and_args_unchanged() -> None:
    url, remaining = apply_url_placeholders(
        "https://gateway.example/api/bills",
        {"period_ym": "2026-09"},
    )

    assert url == "https://gateway.example/api/bills"
    assert remaining == {"period_ym": "2026-09"}


def test_apply_url_placeholders_substitutes_query_template() -> None:
    url, remaining = apply_url_placeholders(
        "https://gateway.example/search?org={org_id}&keep=1",
        {"org_id": "42", "page": 1},
    )

    assert url == "https://gateway.example/search?org=42&keep=1"
    assert remaining == {"page": 1}


def test_prepare_http_request_get_puts_leftovers_in_query() -> None:
    request_url, request_kwargs = prepare_http_request("GET", BILL_URL, BILL_ARGS)

    assert request_url == "https://gateway.example/api/platform/organizations/42/consumption-bills"
    assert request_kwargs == {"params": {"period_ym": "2026-09", "page": 1}}


def test_prepare_http_request_post_puts_leftovers_in_json_body() -> None:
    request_url, request_kwargs = prepare_http_request("POST", BILL_URL, BILL_ARGS)

    assert request_url == "https://gateway.example/api/platform/organizations/42/consumption-bills"
    assert request_kwargs == {"json": {"period_ym": "2026-09", "page": 1}}


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_prepare_http_request_other_methods_keep_leftovers_in_json(method: str) -> None:
    request_url, request_kwargs = prepare_http_request(method, BILL_URL, BILL_ARGS)

    assert request_url.endswith("/organizations/42/consumption-bills")
    assert request_kwargs == {"json": {"period_ym": "2026-09", "page": 1}}


def test_prepare_get_request_still_merges_existing_query() -> None:
    request_url, request_kwargs = prepare_get_request(
        "https://gateway.example/bills?source=console",
        {"page": 1},
    )

    assert request_url == "https://gateway.example/bills?source=console&page=1"
    assert request_kwargs == {}


def test_calendar_date_to_unix_uses_shanghai_day_bounds() -> None:
    shaped = apply_request_argument_transforms(
        {
            "organization_id": 10,
            "date_from": "2026-10-22",
            "date_to": "2026-10-22",
            "channel_ids": "12",
        },
        EXPORT_DATE_TRANSFORMS,
    )

    assert shaped == {
        "organization_id": 10,
        "from": 1792598400,
        "to": 1792684800,
        "channel_ids": "12",
    }


def test_prepare_http_request_applies_date_transforms_before_query() -> None:
    request_url, request_kwargs = prepare_http_request(
        "GET",
        "https://www.numwayai.com/api/platform/usage-details/export",
        {
            "organization_id": 10,
            "date_from": "2026-10-22",
            "date_to": "2026-10-22",
        },
        transforms=EXPORT_DATE_TRANSFORMS,
    )

    assert request_url.endswith("/usage-details/export")
    assert request_kwargs == {
        "params": {
            "organization_id": 10,
            "from": 1792598400,
            "to": 1792684800,
        }
    }


def test_calendar_date_to_unix_rejects_bad_date() -> None:
    with pytest.raises(RequestTransformError, match="须为 YYYY-MM-DD") as exc_info:
        apply_request_argument_transforms(
            {"date_from": "10/22/2026"},
            EXPORT_DATE_TRANSFORMS[:1],
        )

    assert exc_info.value.code == "REQUEST_TRANSFORM"


def test_calendar_date_to_unix_skips_missing_source() -> None:
    shaped = apply_request_argument_transforms(
        {"organization_id": 10, "date_to": "2026-10-22"},
        EXPORT_DATE_TRANSFORMS,
    )

    assert shaped == {"organization_id": 10, "to": 1792684800}


def test_resolve_request_transforms_prefers_config_then_schema() -> None:
    from_config = resolve_request_transforms(
        {"request_transforms": EXPORT_DATE_TRANSFORMS},
        {"x-request-transforms": [{"type": "other"}]},
    )
    from_schema = resolve_request_transforms(
        {"execution": {"timeout_seconds": 8}},
        {"x-request-transforms": EXPORT_DATE_TRANSFORMS},
    )

    assert from_config == EXPORT_DATE_TRANSFORMS
    assert from_schema == EXPORT_DATE_TRANSFORMS
