from __future__ import annotations

import inspect

from app.channels.service_intake import _try_handle_knowledge_proposal_confirmation
from app.core.harness_v2_engine import HarnessV2Engine
from app.core.response_generator import ResponseGenerator
from app.knowledge import web_turn


def test_harness_schedules_honcho_after_durable_messages_and_before_planner() -> None:
    source = inspect.getsource(HarnessV2Engine.run)
    user_append = source.index("_append_message")
    user_commit = source.index("self.db.commit()", user_append)
    schedule_user = source.index("schedule_honcho_message", user_commit)
    inject = source.index("inject_honcho_representation", schedule_user)
    planner = source.index("self.planner.plan", inject)
    finalize = source.index("self.owner._finalize_turn", planner)
    assistant_commit = source.index("self.db.commit()", finalize)
    schedule_assistant = source.index("schedule_honcho_message", assistant_commit)

    assert user_append < user_commit < schedule_user < inject < planner
    assert finalize < assistant_commit < schedule_assistant


def test_response_stage_shows_labeled_long_term_portrait() -> None:
    prompt = ResponseGenerator()._stage_payload(
        {
            "user_message": "你好",
            "conversation_context": {"messages": []},
        },
        None,
        memory_context=[
            {"kind": "fact", "content": "用户喜欢简洁"},
            {"kind": "honcho_representation", "content": "长期用户画像\n偏好上午沟通"},
        ],
    )

    memory = prompt["_agent_stage"]["memory"]
    assert "长期用户画像" in memory
    assert "偏好上午沟通" in memory
    assert "用户喜欢简洁" in memory


def test_knowledge_short_circuit_does_not_reference_honcho() -> None:
    assert "honcho" not in inspect.getsource(web_turn).lower()
    assert "honcho" not in inspect.getsource(_try_handle_knowledge_proposal_confirmation).lower()
