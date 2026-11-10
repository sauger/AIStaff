from __future__ import annotations

from app.llm.call_policy import policy_for


# Control-plane routers already have deterministic fallbacks. Retrying an empty
# reasoning-only response delays the real work without improving routing.
OPERATION_EMPTY_RESPONSE_RETRIES: dict[str, int] = {
    "knowledge.document_route": 0,
    "knowledge.bucket_route": 0,
}


def operation_output_tokens(operation: str, configured_tokens: int) -> int:
    """Return the effective output budget for ``operation``.

    An operation policy can only lower the operator-configured Max Tokens value;
    it never raises a deliberately small model configuration.
    """

    configured = max(1, int(configured_tokens or 1))
    policy = policy_for(operation)
    if policy is None:
        return configured
    return max(1, min(configured, policy.max_tokens))


def operation_empty_response_retries(operation: str, default_retries: int) -> int:
    configured = OPERATION_EMPTY_RESPONSE_RETRIES.get(operation)
    return max(0, int(default_retries if configured is None else configured))
