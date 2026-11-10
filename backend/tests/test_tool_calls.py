from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.llm.client import (
    LLMClient,
    ToolCallingUnsupported,
    _tool_calls_from_completion,
)
from app.observability.spans import llm_operation

_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "read a file",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}


class _Completions:
    def __init__(self, *, completion=None, error: BaseException | None = None) -> None:
        self.calls: list[dict] = []
        self.completion = completion
        self.error = error

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.completion


def _client(completions: _Completions) -> LLMClient:
    client = object.__new__(LLMClient)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client.model = "demo-model"
    client.temperature = 0.2
    client.max_output_tokens = 8192
    client.thinking_mode = "disabled"
    return client


def _tool_call(name: str, arguments: str, call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _chat_completion(tool_calls, *, content: str | None = None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def test_generate_tool_calls_returns_decoded_records():
    completions = _Completions(
        completion=_chat_completion([_tool_call("read_file", '{"path": "a.txt"}')])
    )
    client = _client(completions)

    with llm_operation("harness.task_action"):
        records = client.generate_tool_calls(
            "system",
            {"task": "read a file"},
            tools=[_READ_TOOL],
        )

    assert [record.name for record in records] == ["read_file"]
    assert records[0].parsed_arguments == {"path": "a.txt"}
    assert records[0].arguments == '{"path": "a.txt"}'
    assert records[0].id == "call_1"
    call = completions.calls[0]
    assert call["tools"] == [_READ_TOOL]
    assert call["tool_choice"] == "auto"
    assert call["max_tokens"] == 1600
    assert call["timeout"] == 45.0
    assert "response_format" not in call
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}


def test_generate_tool_calls_without_tools_makes_no_request():
    completions = _Completions(completion=_chat_completion([]))
    client = _client(completions)

    assert client.generate_tool_calls("system", "hello", tools=[]) == []
    assert completions.calls == []


def test_plain_text_answer_is_not_a_protocol_failure():
    completions = _Completions(completion=_chat_completion([], content="已完成"))
    client = _client(completions)

    assert client.generate_tool_calls("system", "hi", tools=[_READ_TOOL]) == []


def test_provider_rejecting_tools_raises_dedicated_error():
    completions = _Completions(
        error=RuntimeError("tools is not supported for this model"),
    )
    client = _client(completions)

    with pytest.raises(ToolCallingUnsupported):
        client.generate_tool_calls("system", "hi", tools=[_READ_TOOL])


def test_malformed_arguments_are_preserved_for_the_caller():
    completions = _Completions(
        completion=_chat_completion([_tool_call("read_file", "{not-json}")])
    )
    client = _client(completions)

    record = client.generate_tool_calls("system", "hi", tools=[_READ_TOOL])[0]

    assert record.parsed_arguments == {}
    assert record.arguments == "{not-json}"


def test_responses_style_completion_is_normalized():
    completion = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="function_call",
                name="read_file",
                arguments='{"path": "b.txt"}',
                call_id="resp_1",
            )
        ]
    )

    records = _tool_calls_from_completion(completion)

    assert records[0].name == "read_file"
    assert records[0].parsed_arguments == {"path": "b.txt"}
    assert records[0].id == "resp_1"


def test_anthropic_tool_use_blocks_are_normalized():
    completion = SimpleNamespace(
        content=[
            SimpleNamespace(type="text", text="let me read"),
            SimpleNamespace(
                type="tool_use",
                id="toolu_1",
                name="read_file",
                input={"path": "c.txt"},
            ),
        ]
    )

    records = _tool_calls_from_completion(completion)

    assert records[0].name == "read_file"
    assert records[0].parsed_arguments == {"path": "c.txt"}
    assert records[0].id == "toolu_1"


def test_anthropic_text_only_answer_is_not_a_protocol_failure():
    completion = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    assert _tool_calls_from_completion(completion) == []
