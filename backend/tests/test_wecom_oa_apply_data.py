"""apply_data 控件序列化：Date 必须是 Asia/Shanghai 当日 0 点的 unix 秒。"""

from __future__ import annotations

import pytest

from app.wecom_oa.policy import WecomOaConfigError, build_apply_data_from_fields

_DATE_MAPPING = [
    {
        "control_id": "Date-1",
        "widget_type": "Date",
        "slot_key": "day",
        "required": True,
    }
]


def _date_timestamp(value) -> str:
    data = build_apply_data_from_fields(_DATE_MAPPING, {"day": value})
    content = data["contents"][0]
    assert content["control"] == "Date"
    assert content["id"] == "Date-1"
    assert content["value"]["date"]["type"] == "day"
    return content["value"]["date"]["s_timestamp"]


def test_date_yyyy_mm_dd_becomes_shanghai_midnight_unix():
    # 2026-10-31 00:00:00 Asia/Shanghai
    assert _date_timestamp("2026-10-31") == "1793376000"


@pytest.mark.parametrize(
    "raw",
    ["2026/10/31", "2026.10.31", "2026年10月31日", "2026-10-31T15:30:00"],
)
def test_similar_civil_dates_use_shanghai_midnight(raw: str):
    assert _date_timestamp(raw) == "1793376000"


def test_existing_unix_seconds_pass_through():
    assert _date_timestamp("1793376000") == "1793376000"
    assert _date_timestamp(1793376000) == "1793376000"


def test_unparseable_date_is_rejected_before_applyevent():
    with pytest.raises(WecomOaConfigError, match="日期"):
        _date_timestamp("next friday")
    with pytest.raises(WecomOaConfigError, match="日期"):
        _date_timestamp("2026-10-31 see also 2026-11-01")
