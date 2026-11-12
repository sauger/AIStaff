"""Mail Date header timezone normalization."""

from __future__ import annotations

from datetime import UTC, datetime

from app.mail.transport import _parse_date


def test_parse_date_naive_assumes_utc():
    parsed = _parse_date("Wed, 12 Nov 2025 10:00:00")
    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed == datetime(2025, 11, 12, 10, 0, tzinfo=UTC)


def test_parse_date_aware_converts_to_utc():
    parsed = _parse_date("Wed, 12 Nov 2025 18:00:00 +0800")
    assert parsed == datetime(2025, 11, 12, 10, 0, tzinfo=UTC)


def test_parse_date_empty_and_invalid():
    assert _parse_date("") is None
    assert _parse_date("not-a-date") is None
