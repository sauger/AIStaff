from types import SimpleNamespace

from app.core.harness_capability_invoker import HarnessCapabilityInvoker


def _invoker():
    return SimpleNamespace(
        _external_call_counts={},
        _emit_trace=lambda *_args, **_kwargs: None,
    )


def _tool(harness: dict | None):
    return SimpleNamespace(config_json={"harness": harness} if harness is not None else {})


def test_no_cap_when_unconfigured() -> None:
    invoker = _invoker()
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, _tool(None), "t") is None
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, _tool({}), "t") is None
    assert invoker._external_call_counts == {}


def test_zero_and_bool_limits_are_ignored() -> None:
    invoker = _invoker()
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, _tool({"max_calls_per_turn": 0}), "t") is None
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, _tool({"max_calls_per_turn": True}), "t") is None
    assert invoker._external_call_counts == {}


def test_cap_blocks_after_limit_and_reports_hint() -> None:
    invoker = _invoker()
    tool = _tool({"max_calls_per_turn": 2, "call_limit_hint": "改用 list_platform_organizations"})

    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "erp.account") is None
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "erp.account") is None
    blocked = HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "erp.account")

    assert blocked is not None
    assert blocked["success"] is False
    assert blocked["error"]["code"] == "TOOL_CALL_LIMIT_REACHED"
    assert "erp.account" in blocked["error"]["message"]
    assert "改用 list_platform_organizations" in blocked["error"]["message"]


def test_cap_is_per_tool() -> None:
    invoker = _invoker()
    tool = _tool({"max_calls_per_turn": 1})
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "a") is None
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "b") is None
    assert HarnessCapabilityInvoker._enforce_call_cap(invoker, tool, "a") is not None
