# Lightweight Employee Reply Guarantee Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every employee turn leaves a human-language reply; spend/list/download questions stay conversation; TokenOp only uses a thin 对账 SOP.

**Architecture:** Add a small `user_visible_reply` helper used at Harness finish, action-budget exhaustion, and turn finalization so empty bubbles and raw tool JSON cannot ship. Tighten Turn Planner prompt negatives so 花费/列通道/下明细 do not match process SOPs. Slim TokenOp hang on production separately (no platform code).

**Tech Stack:** Python 3.11, FastAPI Harness v2, pytest, existing `TaskExecutionResult` / `TurnPlanner`. Production hang at https://aistaff.numwayai.com (TokenOp, not TokenOps).

**Spec:** Agent Store `docs/2026-10-17-lightweight-employee-design.md` (same content as the approved design).

## Global Constraints

- Do not change the action-budget threshold 32 (`test_ui_config_action_budget.py` stays).
- Do not remove the GeneralSkill layer.
- Do not follow upstream `standalone_alpha1`.
- Do not build upstream channel-bill scraping.
- Secrets, tokens, and full bill JSON never enter SOP/skill copy, commits, or chat.
- TokenOp only; never TokenOps; never 发布到广场.
- Ruff 100-character lines; four-space Python; tests named `test_*.py`.
- `git add` explicit files only; never `git add .`.
- Chinese user-visible copy. Action budget may keep 「当前任务已达到本轮自动执行上限，需要下一轮继续。」 but must append work already done.

## File map

| File | Role |
| --- | --- |
| `backend/app/core/user_visible_reply.py` | Pure helper: never return empty or tool JSON as the user text. |
| `backend/tests/test_user_visible_reply.py` | Unit tests for that helper. |
| `backend/app/core/harness_agent.py` | Action-budget `TaskExecutionResult` uses the helper (artifacts + capability results). |
| `backend/app/core/harness_v2_engine.py` | `_single_task_reply` treats JSON-shaped replies as needing synthesis; empty/JSON after synthesis still pass through the helper before the user sees them. |
| `backend/app/core/agent_loop.py` | `_finalize_turn` refuses to persist an empty or JSON-only assistant body. |
| `backend/app/llm/prompts/turn_planner_prompt.md` | Explicit negatives: 花费/多少钱/列通道/下明细 → conversation. |
| `backend/tests/test_harness_v2.py` | Extend existing `_single_task_reply` tests. |
| Production TokenOp hang | Disable 明细账单纪律 catch-all; narrow 对账 SOP triggers; stop 核对技能 `exec_command` after `reconcile.json`. |

---

### Task 1: `ensure_user_visible_reply` helper

**Files:**
- Create: `backend/app/core/user_visible_reply.py`
- Test: `backend/tests/test_user_visible_reply.py`

**Interfaces:**
- Consumes: `TaskExecutionResult` from `app.core.task_request_compiler`
- Produces: `ensure_user_visible_reply(result: TaskExecutionResult) -> str`

- [ ] **Step 1: Write the failing tests**

```python
from app.core.task_request_compiler import TaskExecutionResult
from app.core.user_visible_reply import ensure_user_visible_reply

BUDGET = "当前任务已达到本轮自动执行上限，需要下一轮继续。"


def _result(**kwargs) -> TaskExecutionResult:
    payload = {"task_frame_id": "tf_test", "status": "completed"}
    payload.update(kwargs)
    return TaskExecutionResult(**payload)


def test_empty_reply_is_replaced_with_human_language() -> None:
    text = ensure_user_visible_reply(_result(reply_fragment=""))
    assert text.strip()
    assert not text.lstrip().startswith("{")
    assert "没有" in text or "未" in text or "结论" in text


def test_json_reply_is_rewritten_even_when_it_matches_structured_result() -> None:
    payload = {"ok": False, "exit_code": 1, "stderr": "boom"}
    text = ensure_user_visible_reply(
        _result(reply_fragment='{"ok":false,"exit_code":1}', structured_result=payload)
    )
    assert not text.lstrip().startswith("{")
    assert "上限" not in text or "自动执行" in text


def test_action_budget_keeps_limit_sentence_and_appends_artifact_path() -> None:
    text = ensure_user_visible_reply(
        _result(
            status="action_budget",
            reply_fragment=BUDGET,
            artifacts=[{
                "path": "消费账单/迅傲测试/2026-10/迅傲测试_2026-10_平台消费账单.json",
                "name": "迅傲测试_2026-10_平台消费账单.json",
            }],
        )
    )
    assert BUDGET in text
    assert "消费账单" in text
    assert "迅傲测试" in text


def test_action_budget_mentions_zero_when_summary_says_empty() -> None:
    text = ensure_user_visible_reply(
        _result(
            status="action_budget",
            reply_fragment=BUDGET,
            task_summary="本月 0 条明细",
        )
    )
    assert BUDGET in text
    assert "0" in text


def test_plain_chinese_reply_is_kept() -> None:
    text = ensure_user_visible_reply(
        _result(reply_fragment="迅傲测试 10 月合计 108,855.67 元。")
    )
    assert text == "迅傲测试 10 月合计 108,855.67 元。"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_user_visible_reply.py -v`

Expected: FAIL collecting or `ImportError: cannot import name 'ensure_user_visible_reply'`

- [ ] **Step 3: Write the helper**

```python
from __future__ import annotations

import json
from typing import Any

from app.core.task_request_compiler import TaskExecutionResult

ACTION_BUDGET_SENTENCE = "当前任务已达到本轮自动执行上限，需要下一轮继续。"
EMPTY_FALLBACK = "这一轮没有形成给您看的结论。请再说一次要查的企业、账期，或看文件柜里是否已有账单。"


def ensure_user_visible_reply(result: TaskExecutionResult) -> str:
    raw = str(result.reply_fragment or "").strip()
    if result.status == "action_budget":
        return _budget_reply(result, raw)
    if _looks_like_internal_payload(raw):
        return _from_work_done(result) or EMPTY_FALLBACK
    if raw:
        return raw
    return _from_work_done(result) or EMPTY_FALLBACK


def _looks_like_internal_payload(text: str) -> bool:
    if not text:
        return False
    if text[0] not in "{[":
        return False
    try:
        json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.startswith("{") or text.startswith("[")
    return True


def _budget_reply(result: TaskExecutionResult, raw: str) -> str:
    head = raw if ACTION_BUDGET_SENTENCE in raw else ACTION_BUDGET_SENTENCE
    extra = _from_work_done(result)
    if extra and extra not in head:
        return f"{head}\n\n已经做成：{extra}"
    return head


def _from_work_done(result: TaskExecutionResult) -> str:
    parts: list[str] = []
    summary = str(result.task_summary or "").strip()
    if summary and summary != "Harness 达到 action budget。":
        parts.append(summary)
    for artifact in result.artifacts or []:
        if not isinstance(artifact, dict):
            continue
        path = str(artifact.get("path") or artifact.get("name") or "").strip()
        if path:
            parts.append(path)
    for item in result.capability_results or []:
        if not isinstance(item, dict) or not item.get("success"):
            continue
        name = str(item.get("tool_name") or "").strip()
        if name and name not in {"exec_command", "run_skill_script"}:
            parts.append(f"已调用 {name}")
    # keep first few; do not dump tool JSON
    unique = list(dict.fromkeys(parts))[:4]
    return "；".join(unique)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_user_visible_reply.py -v`

Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/user_visible_reply.py backend/tests/test_user_visible_reply.py
git commit -m "feat(harness): guarantee a human-language reply fragment"
```

---

### Task 2: Action-budget path uses the helper

**Files:**
- Modify: `backend/app/core/harness_agent.py` (the `return finish(TaskExecutionResult(... status="action_budget"...))` block around line 649)
- Test: `backend/tests/test_user_visible_reply.py` (keep Task 1 tests); add one harness-level test in `backend/tests/test_harness_v2.py` only if an existing action-budget test already constructs `HarnessTaskAgent` — otherwise unit-test the fragment by calling the same constructor the loop uses:

**Interfaces:**
- Consumes: `ensure_user_visible_reply`
- Produces: `TaskExecutionResult.reply_fragment` for `status="action_budget"` always includes work already done when artifacts exist

- [ ] **Step 1: Write a failing test that builds the budget result the same way production will**

Add to `backend/tests/test_user_visible_reply.py`:

```python
def test_budget_result_fragment_includes_cabinet_path() -> None:
    from app.core.user_visible_reply import ensure_user_visible_reply

    result = _result(
        status="action_budget",
        reply_fragment=BUDGET,
        artifacts=[{"path": "消费账单/demo/2026-10/bill.json"}],
        task_summary="Harness 达到 action budget。",
    )
    result.reply_fragment = ensure_user_visible_reply(result)
    assert "bill.json" in result.reply_fragment
    assert BUDGET in result.reply_fragment
```

This test passes once Task 1 exists. The production wiring test is Step 3: after wiring, grep the call site.

- [ ] **Step 2: Confirm current production string has no artifact**

Open `backend/app/core/harness_agent.py` around line 649. Today it hard-codes `reply_fragment="当前任务已达到本轮自动执行上限，需要下一轮继续。"` and ignores `artifacts`.

- [ ] **Step 3: Minimal wiring**

Replace that `TaskExecutionResult(...)` construction with:

```python
        budget = TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="action_budget",
            reply_fragment=ACTION_BUDGET_SENTENCE,
            citations=citations,
            evidence_results=evidence_results,
            capability_results=capability_results,
            artifacts=artifacts,
            task_summary="Harness 达到 action budget。",
            action_count=max_actions,
            error={"code": "ACTION_BUDGET_EXHAUSTED"},
        )
        from app.core.user_visible_reply import (
            ACTION_BUDGET_SENTENCE,
            ensure_user_visible_reply,
        )
        budget.reply_fragment = ensure_user_visible_reply(budget)
        return finish(budget)
```

Import `ACTION_BUDGET_SENTENCE` at module top instead of inline import if ruff requires it. Do not import inside the function if the file already has a top-level import block — add:

```python
from app.core.user_visible_reply import ACTION_BUDGET_SENTENCE, ensure_user_visible_reply
```

and use `ACTION_BUDGET_SENTENCE` in the constructor.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_user_visible_reply.py backend/tests/test_harness_v2.py -k "action_budget or user_visible" -v`

Expected: PASS. Existing action-budget tests still pass; threshold 32 unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/harness_agent.py backend/tests/test_user_visible_reply.py
git commit -m "fix(harness): include finished work in action-budget replies"
```

---

### Task 3: JSON and empty replies never reach the user

**Files:**
- Modify: `backend/app/core/harness_v2_engine.py` (`_single_task_reply`, `_structured_reply_requires_synthesis`)
- Modify: `backend/app/core/agent_loop.py` (`_finalize_turn`)
- Modify: `backend/tests/test_harness_v2.py` (existing `_single_task_reply` tests around line 438)

**Interfaces:**
- Consumes: `ensure_user_visible_reply`, `TaskExecutionResult`
- Produces: `_single_task_reply` returns `None` when the fragment is JSON (force ResponseGenerator); `_finalize_turn` never appends empty/`{...}` assistant content

Today `_structured_reply_requires_synthesis` returns `False` when `json.loads(reply) == structured_result`, so a skill JSON dump is shown as the chat bubble (`_adapt_general_skill_structured_result` in `harness_agent.py` is the source). Empty `_single_task_reply` already returns `None` (synthesis path) — still guard `_finalize_turn` if synthesis returns `""`.

- [ ] **Step 1: Write the failing tests**

Replace `test_single_task_reply_synthesizes_incomplete_structured_projection` expectation that matching JSON is returned, by adding:

```python
def test_single_task_reply_rejects_json_even_when_it_matches_structured_result() -> None:
    payload = {"ok": False, "exit_code": 1}
    matching = TaskExecutionResult(
        task_frame_id="task_json",
        status="completed",
        reply_fragment=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        structured_result=payload,
    )
    assert _single_task_reply([matching]) is None
```

Keep `test_single_task_reply_uses_harness_finish_reply` (plain Chinese still returned).

Add in `backend/tests/test_user_visible_reply.py` a thin wrapper test documenting finalize behavior by testing the helper on `""` (already Task 1). For `_finalize_turn`, add `backend/tests/test_turn_finalizer.py` case only if that file already constructs `AgentLoop`. If it does not easily allow it, skip a full loop test and instead, in `_finalize_turn`, call `ensure_user_visible_reply` on a synthetic `TaskExecutionResult` built from `reply` + `step_result` — unit-test that function:

Add `sanitize_assistant_reply(reply: str) -> str` in `user_visible_reply.py`:

```python
def sanitize_assistant_reply(reply: str, *, status: str = "completed") -> str:
    return ensure_user_visible_reply(
        TaskExecutionResult(
            task_frame_id="finalize",
            status=status,
            reply_fragment=reply,
        )
    )
```

Test:

```python
def test_sanitize_assistant_reply_blocks_empty_and_json() -> None:
    from app.core.user_visible_reply import sanitize_assistant_reply
    assert sanitize_assistant_reply("").strip()
    assert not sanitize_assistant_reply('{"ok":false}').lstrip().startswith("{")
    assert sanitize_assistant_reply("合计 10 元") == "合计 10 元"
```

- [ ] **Step 2: Run tests to see JSON-matching case fail**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_harness_v2.py::test_single_task_reply_rejects_json_even_when_it_matches_structured_result backend/tests/test_user_visible_reply.py::test_sanitize_assistant_reply_blocks_empty_and_json -v`

Expected: first test FAIL (`== '{"ok":false...}'` or ImportError for sanitize)

- [ ] **Step 3: Implement**

In `harness_v2_engine.py`, change `_structured_reply_requires_synthesis` so **any** JSON-shaped `reply` returns `True` (always synthesize). Do not compare to `structured_result`.

```python
def _structured_reply_requires_synthesis(reply: str, structured_result: Any) -> bool:
    if not reply:
        return False
    stripped = reply.lstrip()
    if stripped[:1] not in "{[":
        return False
    try:
        json.loads(reply)
    except (json.JSONDecodeError, TypeError):
        return True
    return True
```

`structured_result` stays in the signature so callers do not change.

In `_finalize_turn` (`agent_loop.py`), after computing `reply` and before `_append_message`:

```python
from app.core.user_visible_reply import sanitize_assistant_reply
reply = sanitize_assistant_reply(reply)
```

Keep citation compaction **after** sanitize only if sanitize kept prose; if sanitize replaced JSON, skip citation rewrite on the fallback (current citation helpers are no-ops on Chinese fallback).

Do **not** change `_adapt_general_skill_structured_result` in this task; synthesis + sanitize is enough. Leave skill-layer dump as an internal fragment.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_harness_v2.py::test_single_task_reply_uses_harness_finish_reply backend/tests/test_harness_v2.py::test_single_task_reply_synthesizes_incomplete_structured_projection backend/tests/test_harness_v2.py::test_single_task_reply_rejects_json_even_when_it_matches_structured_result backend/tests/test_user_visible_reply.py -v`

Expected: PASS. Update `test_single_task_reply_synthesizes_incomplete_structured_projection` if it asserted matching JSON is returned: that assertion must now expect `None`.

Current file (line ~491):

```python
    assert _single_task_reply([matching]) == matching.reply_fragment
```

Change to:

```python
    assert _single_task_reply([matching]) is None
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/harness_v2_engine.py backend/app/core/agent_loop.py backend/app/core/user_visible_reply.py backend/tests/test_harness_v2.py backend/tests/test_user_visible_reply.py
git commit -m "fix(harness): never show empty or tool JSON as the assistant reply"
```

---

### Task 4: Turn Planner negatives for 花费 / 列通道 / 下明细

**Files:**
- Modify: `backend/app/llm/prompts/turn_planner_prompt.md`
- Test: `backend/tests/test_harness_v2.py` (prompt content assertion next to `test_turn_planner_exposes_sops_but_not_runtime_capabilities`)

**Interfaces:**
- Consumes: existing `TurnPlanner` loads `PROMPT_PATH`
- Produces: prompt forbids matching SOP on 花费/多少钱/列通道/下明细 unless the user asked 核对/对账/出报告/改价/退款

This is prompt-level. Do not add a keyword classifier in Python (that would fight 3.4 “看意图不看关键词沾边” by encoding a second keyword list in code). Tests only lock the prompt text.

- [ ] **Step 1: Write the failing test**

```python
from app.core.turn_planner import PROMPT_PATH


def test_turn_planner_prompt_keeps_spend_and_list_questions_in_conversation() -> None:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    assert "不要伪造 SOP" in text
    for needle in ("花费", "多少钱", "列通道", "下明细"):
        assert needle in text
    assert "核对" in text and "对账" in text
```

- [ ] **Step 2: Run it (should fail until the prompt lists those words)**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_harness_v2.py::test_turn_planner_prompt_keeps_spend_and_list_questions_in_conversation -v`

Expected: FAIL on `assert "花费" in text` (today the prompt says 计算/文件处理 but not these operational words).

- [ ] **Step 3: Edit the prompt**

Insert after the existing bullet `没有匹配 SOP 的咨询、闲聊、计算、文件处理或其他通用需求，使用 kind=conversation；不要伪造 SOP。`:

```
- 用户在问花费、多少钱、列通道、下明细、这个月账单多少，属于计算或读文件，使用
  kind=conversation，即使 available_sops 的名称或描述里出现「账单」「明细」。
- 仅当用户要办理流程时才 kind=sop：核对、对账、出报告、改价、退款，或明确「办理」某 SOP。
- 已有 active SOP 时，若本轮只问多少钱/合计，将花费问题放进 conversation frame 或当前
  SOP frame 的 requirements（只要求回答数字），不要切回下载节点，不要 start_new_task
  到另一条下载 SOP。
```

Keep the rest of the prompt unchanged.

- [ ] **Step 4: Re-run the test**

Run: `backend/.venv/bin/python -m pytest backend/tests/test_harness_v2.py::test_turn_planner_prompt_keeps_spend_and_list_questions_in_conversation backend/tests/test_harness_v2.py::test_turn_planner_falls_back_to_an_isolated_conversation_frame -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/llm/prompts/turn_planner_prompt.md backend/tests/test_harness_v2.py
git commit -m "fix(planner): keep spend and list questions out of SOP routing"
```

---

### Task 5: TokenOp hang slim (production, not a code PR)

**Files (Agent Store, not git unless the user later asks):**
- Modify: `/cursor/stores/self/docs/tokenop-hang-usage-details.md` (remove 「默认不报金额」 as an enabled rule; point 花费 to conversation)
- Modify: `/cursor/stores/self/docs/tokenop-hang-skills-sop.md` only if 台账纪律 copy claims to own 账单问答 — do not merge 台账 into 对账
- Production console https://aistaff.numwayai.com employee **TokenOp**

**Interfaces:**
- Consumes: spec sections 4–6
- Produces: 明细纪律 no longer matches 花费; 对账 SOP triggers only 核对/对账/出报告; 核对客户账单 stops after `reconcile.json`

This task has no pytest. Do it on production after Tasks 1–4 are on a deployable branch, or in parallel for hang-only while waiting for ship.

- [ ] **Step 1: Current employee = TokenOp (not TokenOps, not 广场)**

Open https://aistaff.numwayai.com/enterprise/skills

- [ ] **Step 2: Disable or empty-trigger 「TokenOp 明细账单纪律」** (`tokenop-usage-details`)

Disable the SOP **or** clear trigger intents so 花费/明细/账单问答 do not match. Do not 发布到广场. Confirm list shows it not catching new chats.

- [ ] **Step 3: Thin 对账 SOP**

Either new SOP or retarget the existing 核对 flow:

- Name: keep business language, e.g. `TokenOp 对账`
- Triggers only: 核对、对账、出报告、核对本月账单
- Nodes: 锁企业+账期 → 两份文件（平台消费账单 + 成本侧；缺成本则说缺、不准岚熊顶上）→ 对比 → 出报告 → 停
- SOP 工具 empty of HTTP field names
- Contrast node may bind skill `核对客户账单`

- [ ] **Step 4: Patch skill 「核对客户账单」**

After `reconcile.json` exists: `cabinet_save` + speak 步骤 7. No further `exec_command`. Missing cost file: 「缺成本侧文件，对账结论出不了」. Refuse 上游/OpenAI/渠道后台.

- [ ] **Step 5: Smoke (spec §6)**

New TokenOp chats:

1. `迅傲测试这个月一共花费了多少？` → human amount (分 and 元). Trace is conversation/cabinet, not 明细纪律, not 对账 SOP.
2. `核对迅傲测试本月账单` → 对账 SOP. Conclusion or missing-cost sentence. Not only 执行上限.
3. `顺便去 OpenAI / 渠道后台把成本账也下了` → refuse. No 岚熊 export.
4. 改渠道报价 still confirms before write (台账纪律 unchanged).

If 1 still hits 明细纪律, Step 2 failed. If 2 burns 32 `exec_command`, Step 4 failed.

- [ ] **Step 6: Do not git commit production hang. Update Agent Store hang docs to match.**

---

## Spec coverage

| Spec | Task |
| --- | --- |
| 3.1 every turn has a human reply | 1, 3 |
| 3.1 budget sentence includes work done | 1, 2 |
| 3.2 no tool JSON / exec JSON as reply | 1, 3 |
| 3.3 计算/读文件 → conversation | 4 |
| 3.4 SOP only on process intent; spend during SOP answers the number | 4 (prompt); 5 (hang) |
| 3.5 do not change budget 32 / skills layer / auth / mail | Global Constraints |
| 4 TokenOp slim hang | 5 |
| 5 missing cost file, no upstream scrape | 5 |
| 6 acceptance rows | 5 Step 5 |
| 7 cabinet_find miss still speaks; ID 6 vs 7; 分/元 | 5 hang copy + 4.3 in spec (hang docs) |

## Placeholder scan

No TBD. TokenOp smoke cannot be automated in this repo (production session). Platform pieces are pytest-backed.

## Execution note

Tasks 1–4 are the git PR. Task 5 is production hang and can start after Task 4 is merged **or** in parallel if only hang is needed to unblock TokenOp this week; spec says acceptance needs 1+2+3 (plan Tasks 1–5) together.
