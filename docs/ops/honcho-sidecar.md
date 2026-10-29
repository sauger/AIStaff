# Honcho sidecar

Honcho v1 is an optional long-term portrait beside local `MemoryService`. AIStaff does not vendor or patch Honcho. The client calls the **`/v3`** API (`/v3/workspaces`, peers, sessions, `/messages`, representation). A `/v2` server returns 404 and the turn fails open with no portrait.

## Pin

Install this release, not floating `main`:

- Repo: https://github.com/plastic-labs/honcho
- Tag: `v3.4.1`
- Commit: `a92650bc290fbe80b1d4e0fd4ae55b4b4c364efe`

That tag serves `/v3`. AIStaff does not speak `/v2`.

## Disk

The production system disk is nearly full. `/data` is still on that system disk. The data disk mount is `/mnt/data`.

Put the Honcho checkout, Compose project, and its data on that disk:

- Compose project directory: `/mnt/data/honcho`
- Bind mounts (preferred) under `/mnt/data/honcho/volumes/`

Do not clone Honcho onto `/` or `/data`, and do not leave Postgres, Redis, or LanceDB on Docker named volumes if the Docker data root is the system disk. Named volumes follow Docker's data root. Bind them onto `/mnt/data` instead.

```bash
sudo mkdir -p /mnt/data/honcho/volumes/postgres \
  /mnt/data/honcho/volumes/redis \
  /mnt/data/honcho/volumes/lancedb
git clone https://github.com/plastic-labs/honcho.git /mnt/data/honcho
cd /mnt/data/honcho
git checkout v3.4.1
# a92650bc290fbe80b1d4e0fd4ae55b4b4c364efe
cp docker-compose.yml.example docker-compose.yml
cp .env.template .env
```

Point the data services at the bind mounts. The container path `/data` on Redis is inside the container. The host path stays `/mnt/data/...`.

```yaml
services:
  database:
    volumes:
      - /mnt/data/honcho/volumes/postgres:/var/lib/postgresql/data
  redis:
    volumes:
      - /mnt/data/honcho/volumes/redis:/data
  api:
    volumes:
      - /mnt/data/honcho/volumes/lancedb:/app/lancedb_data
  deriver:
    volumes:
      - /mnt/data/honcho/volumes/lancedb:/app/lancedb_data
```

Drop the `mcp` service if you do not need it. Run Compose from `/mnt/data/honcho` so the project directory is on the data disk too.

## Deriver

Representation stays empty until the deriver can call a model. `v3.4.1` `.env.template` uses these names. Values below are the template defaults, not live secrets. Leave the key empty until you set a real one outside git.

```env
LLM_OPENAI_API_KEY=
DERIVER_ENABLED=true
DERIVER_MODEL_CONFIG__TRANSPORT=openai
DERIVER_MODEL_CONFIG__MODEL=gpt-5.4-mini
# OpenAI-compatible endpoint (OpenRouter, vLLM, etc.):
# DERIVER_MODEL_CONFIG__MODEL=your-model-here
# DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://openrouter.ai/api/v1
# DERIVER_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=LLM_OPENAI_API_KEY
# EMBEDDING_MODEL_CONFIG__MODEL=text-embedding-3-small
```

`LLM_ANTHROPIC_API_KEY` and `LLM_GEMINI_API_KEY` exist for the other transports. Set one provider key. Do not commit it.

## Runtime settings

Admins can set the deriver and embedding models in **运行设置 → 长期记忆 / Honcho**, next to the master switch. These are Honcho's own model settings. They are not AIStaff's model key, and they are not `HONCHO_API_KEY` (the optional bearer token AIStaff uses when calling Honcho).

| UI label | Saved column | Sidecar env key |
| --- | --- | --- |
| 对话抽取模型 ID | `honcho_deriver_model` | `DERIVER_MODEL_CONFIG__MODEL` |
| 对话抽取接口地址 | `honcho_deriver_base_url` | `DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL` |
| 对话抽取 API 密钥 | `honcho_deriver_api_key_encrypted` | `LLM_OPENAI_API_KEY` |
| 向量嵌入模型 ID | `honcho_embedding_model` | `EMBEDDING_MODEL_CONFIG__MODEL` |
| 向量嵌入接口地址 | `honcho_embedding_base_url` | `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` |
| 向量嵌入 API 密钥 | `honcho_embedding_api_key_encrypted` | `EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY` |
| 向量维度 | `honcho_embedding_dimensions` | `EMBEDDING_VECTOR_DIMENSIONS` (default 1536) |

Transport is always `openai` when a section is applied, which covers OpenAI-compatible endpoints such as DeepSeek and 阿里云百炼. A typical China setup is `deepseek-chat` at `https://api.deepseek.com` for 对话抽取, and `text-embedding-v4` at `https://dashscope.aliyuncs.com/compatible-mode/v1` with vector dimensions 1536 for 向量嵌入. 向量嵌入 is the embedding model, not a tokenizer.

API keys are encrypted with `APP_SECRET` before they are stored. Reads return `honcho_deriver_api_key_set` / `honcho_embedding_api_key_set` and never the plaintext. Submitting an empty key leaves the stored secret unchanged and does not overwrite that key in the sidecar file.

Applying a deriver model also points dialectic (`minimal` through `max`), summary, and dream deduction/induction at the same chat model and base URL. Those features keep using `LLM_OPENAI_API_KEY`. Embedding keeps its own key when one is saved, so it does not have to share the chat key.

The sidecar file is process-wide. The last successful apply wins for that Honcho, even if more than one tenant has saved different models.

### Apply and restart

Set this on the AIStaff process, not in Honcho:

```env
HONCHO_SIDECAR_ENV_FILE=/mnt/data/honcho/.env
```

The path must be absolute and the AIStaff user must be able to write it. AIStaff rewrites only the keys in the table above (plus the matching dialectic, summary, and dream keys) and leaves the rest of the file, including comments, in place. The file mode is set to `0600`.

- When the path is set, saving 运行设置 writes the file if a deriver or embedding model is saved.
- When the path is empty, the save still stays in `ui_configs`. **应用到 sidecar** returns a clear error naming `HONCHO_SIDECAR_ENV_FILE` and does not create a file.
- Honcho does not reload `.env` live. From `/mnt/data/honcho`, restart the API and the deriver:

```bash
docker compose up -d --force-recreate api deriver
```

Do not change the embedding model or `EMBEDDING_VECTOR_DIMENSIONS` after vectors exist. The column width and the vectors already stored stop matching, and the vector store has to be rebuilt. If a provider rejects structured output, add `DERIVER_MODEL_CONFIG__STRUCTURED_OUTPUT_MODE=json_object` by hand; that line is preserved. If 百炼 rejects a large embedding batch, add `EMBEDDING_MODEL_CONFIG__MAX_BATCH_SIZE=10` the same way.

## Start

1. Checkout `v3.4.1` under `/mnt/data/honcho` and apply the bind mounts above.
2. Fill `.env` on that disk. The deriver reads `DERIVER_MODEL_CONFIG__MODEL`.
3. Start order: Postgres and Redis, then the API, then the deriver. From `/mnt/data/honcho`: `docker compose up -d`. AIStaff starts after `GET /health` on the API succeeds.
4. In AIStaff set `HONCHO_ENABLED=true`, `HONCHO_BASE_URL` (`http://127.0.0.1:8000` or the Compose service URL), and `HONCHO_API_KEY` only if that Honcho deployment requires a bearer token. `HONCHO_TIMEOUT_MS` defaults to 200 and applies only to the synchronous portrait read. Set `HONCHO_SIDECAR_ENV_FILE` to this checkout's `.env` if admins should apply deriver and embedding settings from 运行设置.
5. In 运行设置, save the 对话抽取 and 向量嵌入 fields, then restart Honcho `api` and `deriver` as above. Turn on the Honcho master switch and `honcho_enabled` for one employee in the profile editor. Both switches must be on. Group chats are skipped.

```env
HONCHO_ENABLED=true
HONCHO_BASE_URL=http://127.0.0.1:8000
HONCHO_API_KEY=
HONCHO_TIMEOUT_MS=200
HONCHO_WORKSPACE_PREFIX=tenant:
```

## Identity

Logical ids are `tenant:{tenant_id}`, `user:{User.id}`, `agent:{agent_id}`, and `chat:{ChatSession.id}`. Honcho resource names cannot contain `:`, so the wire id replaces that one separator with `_` (`user:user_abc` becomes `user_user_abc`). `staff_peer` / `staff_session` / `staff_workspace` store the logical id.

Peer ensure sends only `staff_peer`. Honcho replaces peer metadata wholesale, so channel fields are not written there. Channel, account scope, and external user id are message metadata only. Two channels that have not already been bound to the same `User.id` stay two peers. This release does not merge them.

## Operations

- Health: `GET /health` on the Honcho API.
- Backup `/mnt/data/honcho/volumes/postgres`. That directory holds messages and derived portraits.
- A timeout or HTTP 5xx is logged and skipped. The chat turn still completes.
- Leave `HONCHO_ENABLED` false until the API and deriver are healthy.
