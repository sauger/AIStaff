from dataclasses import FrozenInstanceError

import pytest

from app.llm.call_policy import CallPolicy, policy_for


@pytest.mark.parametrize(
    "operation",
    [
        "harness.task_action",
        "harness.plan_compile",
        "plan.replan",
        "plan.synthesize",
        "turn_planner.plan",
        "knowledge.document_route",
        "knowledge.bucket_route",
        "memory.capture",
        "session.title",
    ],
)
def test_known_operation_has_a_bounded_policy(operation):
    policy = policy_for(operation)

    assert isinstance(policy, CallPolicy)
    assert policy.max_tokens > 0
    assert policy.timeout_seconds is not None
    assert policy.timeout_seconds > 0
    assert policy.thinking == "disabled"


def test_unknown_operation_keeps_model_default():
    assert policy_for("response.generate") is None
    assert policy_for("") is None
    assert policy_for(None) is None


def test_policy_is_immutable():
    policy = policy_for("harness.task_action")
    assert policy is not None

    with pytest.raises(FrozenInstanceError):
        policy.max_tokens = 1  # type: ignore[misc]
