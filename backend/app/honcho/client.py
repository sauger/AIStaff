"""HTTP client for a self-hosted Honcho API. Errors are normalized for fail-open."""

from __future__ import annotations

from typing import Any

import httpx

WRITE_TIMEOUT_SECONDS = 5.0


class HonchoError(Exception):
    """Base error for Honcho calls."""


class HonchoTimeout(HonchoError):
    """The Honcho call exceeded its timeout."""


class HonchoRequestError(HonchoError):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        super().__init__(f"honcho {status_code}: {detail}")


class HonchoClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = WRITE_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._transport = transport

    def ensure_workspace(self, workspace_id: str, metadata: dict[str, Any] | None = None) -> None:
        self._request(
            "POST",
            "/v2/workspaces",
            {"id": workspace_id, "metadata": metadata or {}},
        )

    def ensure_peer(
        self,
        workspace_id: str,
        peer_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/v2/workspaces/{workspace_id}/peers",
            {"id": peer_id, "metadata": metadata or {}},
        )

    def ensure_session(
        self,
        workspace_id: str,
        session_id: str,
        peer_ids: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/v2/workspaces/{workspace_id}/sessions",
            {
                "id": session_id,
                "metadata": metadata or {},
                "peers": {peer_id: {} for peer_id in peer_ids},
            },
        )

    def add_message(
        self,
        workspace_id: str,
        session_id: str,
        *,
        peer_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._request(
            "POST",
            f"/v2/workspaces/{workspace_id}/sessions/{session_id}/messages/",
            {
                "messages": [
                    {
                        "content": content,
                        "peer_id": peer_id,
                        "metadata": metadata or {},
                    }
                ]
            },
        )

    def fetch_representation(
        self,
        workspace_id: str,
        peer_id: str,
        *,
        timeout_seconds: float,
    ) -> str:
        # Omit session_id so the read is the peer's cross-session portrait.
        payload = self._request(
            "POST",
            f"/v2/workspaces/{workspace_id}/peers/{peer_id}/representation",
            {},
            timeout_seconds=timeout_seconds,
        )
        return representation_text(payload)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
    ) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=timeout,
                transport=self._transport,
                headers=headers,
            ) as http:
                response = http.request(method, path, json=body)
        except httpx.TimeoutException as exc:
            raise HonchoTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise HonchoRequestError(0, str(exc)) from exc
        if response.status_code >= 400:
            raise HonchoRequestError(response.status_code, response.text[:300])
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None


def representation_text(payload: object) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        return "\n".join(part for item in payload if (part := representation_text(item)))
    if not isinstance(payload, dict):
        return ""
    if "representation" in payload:
        nested = representation_text(payload.get("representation"))
        if nested:
            return nested
    lines: list[str] = []
    for key in ("explicit", "deductive", "inductive", "conclusions"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(value.strip())
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str) and item.strip():
                    lines.append(item.strip())
                elif isinstance(item, dict):
                    text = str(item.get("content") or item.get("text") or "").strip()
                    if text:
                        lines.append(text)
    return "\n".join(lines)
