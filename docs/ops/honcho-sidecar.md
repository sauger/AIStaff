# Honcho sidecar

Honcho v1 is an optional long-term portrait beside local `MemoryService`. AIStaff does not vendor or patch Honcho. Run the upstream stack and point AIStaff at it.

## Start

1. Clone [plastic-labs/honcho](https://github.com/plastic-labs/honcho) and use its `docker-compose.yml.example`. The API and deriver images build from that repo. Drop the `mcp` service if you do not need it.
2. Copy `.env.template` to `.env` and set the deriver LLM key (`LLM_OPENAI_API_KEY`, or an OpenAI-compatible base URL and model). Representation stays empty until the deriver can call a model.
3. Start order: Postgres and Redis, then the API, then the deriver. AIStaff starts after `GET /health` on the API returns success.
4. In AIStaff set `HONCHO_ENABLED=true`, `HONCHO_BASE_URL` (for example `http://honcho-api:8000` on a shared Docker network, or `http://127.0.0.1:8000`), and `HONCHO_API_KEY` only if that Honcho deployment requires a bearer token. `HONCHO_TIMEOUT_MS` defaults to 200 and applies only to the synchronous portrait read.
5. Turn on `honcho_enabled` for one employee in the profile editor. The master switch and the employee switch must both be on. Group chats are skipped.

```env
HONCHO_ENABLED=true
HONCHO_BASE_URL=http://127.0.0.1:8000
HONCHO_API_KEY=
HONCHO_TIMEOUT_MS=200
HONCHO_WORKSPACE_PREFIX=tenant:
```

## Identity

Logical ids are `tenant:{tenant_id}`, `user:{User.id}`, `agent:{agent_id}`, and `chat:{ChatSession.id}`. Honcho resource names cannot contain `:`, so the wire id replaces that one separator with `_` (`user:user_abc` becomes `user_user_abc`). The logical id is also stored as `staff_peer` / `staff_session` / `staff_workspace` metadata.

Channel external ids are metadata only. Two channels that have not already been bound to the same `User.id` stay two peers. This release does not merge them.

## Operations

- Health: `GET /health` on the Honcho API.
- Backup the Postgres volume. That database holds messages and derived portraits.
- A timeout or HTTP 5xx is logged and skipped. The chat turn still completes.
- Leave `HONCHO_ENABLED` false until the API and deriver are healthy.
