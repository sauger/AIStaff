# Honcho Sidecar Long-term Memory v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optional per-employee Honcho sidecar that dual-writes private/web turns and injects a separately labeled user portrait, without replacing local memory or merging cross-channel users.

**Architecture:** Thin `backend/app/honcho/` adapter. Harness v2 is the only caller: async dual-write after a message is durable, sync representation read with a hard timeout after local `memory_context` is built. Fail-open. Knowledge confirmation short-circuit stays outside this path.

**Tech Stack:** Python 3.11, httpx, existing async job queue, FastAPI settings, React employee profile editor, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-honcho-sidecar-design.md`

## Global Constraints

- `HONCHO_ENABLED` default `false`. Off means zero Honcho network calls.
- `HONCHO_TIMEOUT_MS` default `200`, and it bounds only the sync representation read.
- `HONCHO_WORKSPACE_PREFIX` default `tenant:`.
- Logical ids: workspace `tenant:{tenant_id}`, peer `user:{User.id}` / `agent:{agent_id}`, session `chat:{ChatSession.id}`.
- Channel `(channel, account_scope, external_user_id)` is metadata only. It is never a peer or session key.
- Honcho v1 does not merge wecom / feishu / email / web into one `User.id`. Different `User.id` values stay different peers.
- Skip `channel_is_group`. Keep `MemoryService`. Do not call Honcho from knowledge confirmation.
- Employee flag is `AgentProfile.metadata_json["honcho_enabled"]` boolean true.
- Active only when `HONCHO_ENABLED && metadata.honcho_enabled && not group`.
- Fail-open: timeout or 5xx does not fail the turn or surface an error to the user.
- Do not add a `peer.chat` tool. Do not call Honcho Cloud. Do not read the OA temp identity table.
- Honcho resource names match `^[A-Za-z0-9_-]{1,100}$` (v2). The colon form is the stable logical id; the wire id replaces that one separator with `_`. A raw id that itself contains `:` is rejected, not folded into another peer.
- Ruff 100-character lines. Explicit `git add` paths. Do not stage `design-*.md`.

## File map

| File | Role |
| --- | --- |
| `backend/app/config.py` | Global Honcho settings. |
| `backend/.env.example` | Env names and defaults. |
| `backend/app/honcho/ids.py` | Logical and wire ids, channel metadata. |
| `backend/app/honcho/client.py` | HTTP calls, timeout, error normalization. |
| `backend/app/honcho/service.py` | Gate, inject, dual-write. |
| `backend/app/honcho/jobs.py` | Async enqueue. |
| `backend/app/core/harness_v2_engine.py` | Call sites after durable append and before planner. |
| `backend/app/core/response_generator.py` | Pass `memory_context` into the stage payload. |
| `backend/app/llm/stage_protocol.py` | Render memory for the Response Generator phase. |
| `frontend-enterprise/src/components/EmployeeProfileEditor.tsx` | Employee toggle. |
| `frontend-enterprise/src/i18n/en.json` | English for the new Chinese copy. |
| `docs/ops/honcho-sidecar.md` | Light self-host notes. |
| `backend/tests/test_honcho_*.py` | Mocked acceptance coverage. |

---

### Task 1: Identity mapping

**Files:**
- Create: `backend/app/honcho/ids.py`
- Test: `backend/tests/test_honcho_ids.py`

**Interfaces:**
- Produces: `build_honcho_identity(...) -> HonchoIdentity` with `workspace_logical`, `workspace_id`, `user_peer_logical`, `user_peer_id`, `agent_peer_logical`, `agent_peer_id`, `session_logical`, `session_id`, `user_peer_metadata`, `agent_peer_metadata`, `session_metadata`.
- Produces: `HonchoIdError` when a wire id would be illegal or would collapse two logical ids.

- [x] Write failing tests for colon logical ids, underscore wire ids, metadata-only external ids, and distinct peers for distinct `User.id` values that share an external id.
- [x] Implement `ids.py` until those tests pass.

### Task 2: HTTP client

**Files:**
- Create: `backend/app/honcho/client.py`
- Test: `backend/tests/test_honcho_client.py`

**Interfaces:**
- Produces: `HonchoClient.ensure_workspace`, `ensure_peer`, `ensure_session`, `add_message`, `fetch_representation`.
- Produces: `HonchoTimeout`, `HonchoRequestError`.
- `fetch_representation(workspace_id, peer_id, *, timeout_seconds) -> str`. No `session_id` (cross-session portrait). Write calls use a separate 5s timeout.

- [x] Mock transport tests: bearer auth only when a key is set, message peer id is the user/agent wire id, 5xx and timeout become typed errors, representation text is normalized.
- [x] Implement the client.

### Task 3: Gate, inject, async dual-write

**Files:**
- Create: `backend/app/honcho/service.py`, `backend/app/honcho/jobs.py`, `backend/app/honcho/__init__.py`
- Test: `backend/tests/test_honcho_service.py`

**Interfaces:**
- `schedule_honcho_message(db, request, session, *, role, content, message_id=None, settings=None) -> None`
- `inject_honcho_representation(db, request, session, memory_context, *, settings=None, client=None) -> list`
- `run_honcho_dual_write_job(payload) -> None` logs and swallows Honcho failures.
- Injected block `content` starts with `长期用户画像` and does not remove local memories.

- [x] Tests for §7 items 1, 3, 4, 6, 7, 8 at this layer: master switch, employee switch, group skip, timeout, 5xx, labeled block, external id not in peer id, different users stay different peers.
- [x] Implement service and jobs.

### Task 4: Harness seam and final-answer memory

**Files:**
- Modify: `backend/app/core/harness_v2_engine.py`
- Modify: `backend/app/core/response_generator.py`
- Modify: `backend/app/llm/stage_protocol.py`
- Modify: `backend/tests/test_unified_agent_prompt.py`
- Test: `backend/tests/test_honcho_harness.py`, `backend/tests/test_response_generator.py`

- [x] After the user message commit, schedule a user dual-write, then inject into `memory_context` before `TurnPlanner.plan`. After the assistant commit, schedule an assistant dual-write.
- [x] `ResponseGenerator._stage_payload` passes `memory_context` through. `stage_payload` renders memory for phase `Response Generator`.
- [x] Update the unified-prompt test so the final answer keeps the memory line and Step/Reflection stay empty.
- [x] Assert knowledge web-turn and channel confirmation source do not reference honcho.

### Task 5: Settings, employee toggle, ops note

**Files:**
- Modify: `backend/app/config.py`, `backend/.env.example`
- Modify: `frontend-enterprise/src/components/EmployeeProfileEditor.tsx`, `frontend-enterprise/src/i18n/en.json`
- Create: `frontend-enterprise/src/components/EmployeeProfileEditor.test.tsx`
- Create: `docs/ops/honcho-sidecar.md`

- [x] Settings defaults match the spec. Toggle writes `metadata.honcho_enabled` and preserves other metadata.
- [x] Ops note: upstream compose, deriver LLM key, start order, `GET /health`, Postgres backup, logical-vs-wire ids, no cross-channel merge.

### Task 6: Verify

- [x] `backend/.venv/bin/python -m pytest backend/tests/test_honcho_ids.py backend/tests/test_honcho_client.py backend/tests/test_honcho_service.py backend/tests/test_honcho_harness.py backend/tests/test_response_generator.py backend/tests/test_unified_agent_prompt.py`
- [x] `backend/.venv/bin/ruff check` on the touched Python files.
- [x] `npm --prefix frontend-enterprise test -- src/components/EmployeeProfileEditor.test.tsx` and `npm --prefix frontend-enterprise run i18n:check`.
