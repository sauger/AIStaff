from types import SimpleNamespace

from app.core.harness_progress import ExecutionProgress


def descriptor(**metadata):
    return SimpleNamespace(metadata=metadata, input_schema={})


def test_permanent_failure_is_not_retried_but_new_inputs_can_be_tried():
    guard = ExecutionProgress()
    guard.observe("tool", {"id": 1}, descriptor(), {
        "success": False, "error": {"code": "DENIED", "retryable": False},
    })
    assert guard.before("tool", {"id": 1}, descriptor())["error"]["retryable"] is False
    assert guard.before("tool", {"id": 2}, descriptor()) is None
    assert ExecutionProgress().before("tool", {"id": 1}, descriptor()) is None


def test_transient_failure_allows_two_retries_then_stops():
    guard = ExecutionProgress()
    for _ in range(3):
        assert guard.before("tool", {}, descriptor()) is None
        guard.observe("tool", {}, descriptor(), {
            "success": False, "error": {"code": "TIMEOUT", "retryable": True},
        })
    assert guard.before("tool", {}, descriptor())["error"]["code"] == "TOOL_RETRY_EXHAUSTED"


def test_successful_mail_read_is_not_synchronized_again_and_paging_is_allowed():
    guard = ExecutionProgress()
    guard.observe("mail_list", {"page": 1}, descriptor(), {
        "success": True, "data": {"messages": [{"id": "a"}]},
    })
    assert guard.before("mail_list", {"page": 1}, descriptor()) is not None
    assert guard.before("mail_list", {}, descriptor()) is not None
    assert guard.before("mail_list", {"page": 2}, descriptor()) is None
    guard.observe("mail_list", {"page": 2}, descriptor(), {
        "success": True, "data": {"messages": [{"id": "b"}]},
    })
    assert guard.stalled_actions == 0


def test_synonymous_empty_discovery_queries_hit_no_progress_limit():
    guard = ExecutionProgress()
    for query in ["邮件", "email", "mail search", "邮件搜索"]:
        guard.observe("capability_search", {"query": query}, descriptor(), {
            "success": True, "data": {"matches": [], "match_count": 0},
        })
    assert guard.exhausted


def test_rediscovering_same_tools_does_not_reset_progress():
    guard = ExecutionProgress()
    for i in range(5):
        guard.observe("capability_search", {"query": str(i)}, descriptor(), {
            "success": True, "data": {"matches": ["read_file"]},
        })
    assert guard.exhausted


def test_successful_write_invalidates_read_receipts_and_polling_can_repeat():
    guard = ExecutionProgress()
    guard.observe("read_file", {"path": "x"}, descriptor(), {"success": True, "data": "old"})
    duplicate = guard.before("read_file", {"path": "x"}, descriptor())
    guard.observe("read_file", {"path": "x"}, descriptor(), duplicate)
    guard.observe("write_file", {"path": "x"}, descriptor(side_effect="write"), {
        "success": True, "data": {},
    })
    assert guard.before("read_file", {"path": "x"}, descriptor()) is None
    poll = descriptor(side_effect="read", progress_policy="poll")
    guard.observe("status", {}, poll, {"success": True, "data": {"status": "pending"}})
    assert guard.before("status", {}, poll) is None


def test_unknown_parameters_are_rejected_before_tool_execution():
    guard = ExecutionProgress()
    schema = SimpleNamespace(metadata={}, input_schema={
        "additionalProperties": False, "properties": {"folder": {}},
    })
    assert guard.before("mail_list", {"offset": 20}, schema)["error"]["code"] == "INVALID_ARGUMENTS"


def test_loop_stops_before_fifth_unproductive_model_call(monkeypatch):
    from app.core import harness_agent
    from app.core.task_request_compiler import (
        CapabilityDescriptor,
        CapabilityManifest,
        TaskRequirement,
    )
    from app.db.models import ModelConfig

    generated, invoked = [], []

    class Client:
        def __init__(self, config):
            pass

        def generate_json(self, system, payload):
            generated.append(payload)
            return {"action": "tool", "tool_name": "capability_search",
                    "arguments": {"query": str(len(generated))}}

    monkeypatch.setattr(harness_agent, "LLMClient", Client)

    def invoke(name, arguments):
        invoked.append(name)
        return {"success": True, "data": {"matches": []}}

    requirement = TaskRequirement(task_frame_id="test", kind="conversation", goal="查邮件",
        capability_manifest=CapabilityManifest(available=[CapabilityDescriptor(
            capability_id="discovery", name="capability_search", kind="internal",
        )]))
    result = harness_agent.HarnessTaskAgent().run(
        requirement, ModelConfig(name="fake", provider="openai", model="fake"),
        invoke, max_actions=32,
    )
    assert result.status == "failed"
    assert result.error["code"] == "HARNESS_NO_PROGRESS"
    assert len(generated) == len(invoked) == result.action_count == 4
    assert result.loop_checkpoint


def test_mail_pagination_reaches_underlying_service(monkeypatch):
    from app.mail import harness, header_query

    calls = []
    def listing(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(folder="inbox", empty_reason=None, page=2, page_size=20,
                               total=68, last_error=None, messages=[])
    monkeypatch.setattr(header_query, "list_headers", listing)
    monkeypatch.setattr(harness, "get_mailbox", lambda *args: object())
    invoker = SimpleNamespace(agent_id="employee", db=None, tenant_id="test")
    result = harness.invoke_mail_tool(invoker, "mail_list", {"page": 2, "page_size": 20})
    assert calls[0]["page"] == 2
    assert calls[0]["page_size"] == 20
    assert result["data"]["next_page"] == 3
    assert result["data"]["has_more"]
    for args in [{"offset": 20}, {"page": 0}, {"page_size": 100}, {"page": "2"}]:
        assert harness.invoke_mail_tool(invoker, "mail_list", args)["success"] is False
    assert len(calls) == 1
