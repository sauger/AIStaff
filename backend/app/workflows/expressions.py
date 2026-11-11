"""Parameter expressions for the workflow runtime ($runtime/$slot/$bind/$out).

Resolution is strict by contract: a referenced path that cannot be looked up
raises :class:`UnresolvedExpression` instead of silently producing an empty
string, because a half-filled tool argument is a write hazard on screens.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_EXPRESSION_TOKEN = re.compile(
    r"\$(runtime|slot|bind|out)(?:\.[A-Za-z0-9_\-]+)+"
)
_FULL_EXPRESSION = re.compile(
    r"^\$(runtime|slot|bind|out)(?:\.[A-Za-z0-9_\-]+)+$"
)

_SCOPE_ORDER = ("runtime", "slot", "bind", "out")


class UnresolvedExpression(ValueError):
    """An expression path could not be resolved; never leave it blank."""


@dataclass(frozen=True)
class ExpressionScopes:
    """The four namespaces reachable from node parameter expressions."""

    runtime: Mapping[str, Any] = field(default_factory=dict)
    slot: Mapping[str, Any] = field(default_factory=dict)
    bind: Mapping[str, Any] = field(default_factory=dict)
    out: Mapping[str, Any] = field(default_factory=dict)

    def _lookup(self, scope: str, path: list[str]) -> Any:
        current: Any = getattr(self, scope)
        for key in path:
            if isinstance(current, Mapping) and key in current:
                current = current[key]
            elif isinstance(current, (list, tuple)) and key.isdigit() and int(key) < len(current):
                current = current[int(key)]
            else:
                raise UnresolvedExpression(
                    f"无法解析表达式 ${scope}." + ".".join(path)
                )
        return current

    def resolve_token(self, token: str) -> Any:
        parts = token.lstrip("$").split(".")
        scope, path = parts[0], parts[1:]
        if scope not in _SCOPE_ORDER or not path:
            raise UnresolvedExpression(f"未知表达式作用域：{token}")
        return self._lookup(scope, path)


def render_template(text: str, scopes: ExpressionScopes) -> str:
    """Replace every expression token in a question/reply template.

    Any token that cannot be resolved fails the render — an ask/reply message
    with a silently dropped value would mislead the user.
    """

    def _replace(match: re.Match[str]) -> str:
        return str(scopes.resolve_token(match.group(0)))

    return _EXPRESSION_TOKEN.sub(_replace, text)


def resolve_value(value: Any, scopes: ExpressionScopes) -> Any:
    """Resolve one argument value (dict/list/string) against the scopes.

    A string that is exactly one expression resolves to the referenced value
    with its original type (int/list/...); strings that only embed tokens are
    interpolated; everything else passes through unchanged.
    """

    if isinstance(value, str):
        if _FULL_EXPRESSION.match(value):
            return scopes.resolve_token(value)
        if _EXPRESSION_TOKEN.search(value):
            return render_template(value, scopes)
        return value
    if isinstance(value, dict):
        return {key: resolve_value(item, scopes) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_value(item, scopes) for item in value]
    return value


def resolve_arguments(
    arguments: Mapping[str, Any],
    scopes: ExpressionScopes,
) -> dict[str, Any]:
    return {
        key: resolve_value(item, scopes)
        for key, item in dict(arguments).items()
    }
