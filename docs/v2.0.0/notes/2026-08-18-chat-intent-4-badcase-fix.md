# Phase 3 Live 兑现复盘 — chat_intent 4 个失败 case 修复（2026-08-18）

## 背景与问题

- 上一轮（`2026-08-18-phase3-live-eval-fulfillment.md`）跑出 `chat_intent --live --case intent_04,05,06,07,20` 结果 1/5：仅 `intent_20` 通过，其余 4 个教师端意图失败。
- 失败机制：LLM 在 detail 字段里都明确说出"应使用 `report-generation` / `evaluation-writing` 技能"——意图识别本身正确，**问题在识别后没有可调用的模块入口工具**：
  - intent_04/07：读完 SKILL.md 后停住，0 工具调用。
  - intent_05/06：被 `query_knowledge` 截胡（"成绩单/评语"被当成知识库问答意图）。
- 根因：`MAIN_AGENT_SPEC.allowed_tools` 既没有 `dispatch_module` 路由工具，也没有 `task` subagent 委派入口；`REPORT_AGENT_SPEC`/`EVALUATION_AGENT_SPEC` 仅为 Phase 3 chat 路由预留，未落地。

## 修复方案

扩 `allowed_tools` + 新增 `dispatch_module` 路由工具 + 改 prompt + 扩展 SSE `tool` 事件协议（带 args） + runner 把 `dispatch_module.intent` 映射成 `tool_chain` 元素。

### 修改文件

| 文件 | 改动 |
| --- | --- |
| `python/tools/system/dispatch_module.py` | 新增：`@tool args_schema=DispatchModuleInput`，`intent: Literal["report","evaluation","ppt","image_generate"]`，返回 JSON `{module, hint, payload}` |
| `python/tools/system/__init__.py` | export `dispatch_module` |
| `python/tools/__init__.py` | re-export `dispatch_module` 到顶层 |
| `python/agent/runtime.py` | import + `tool_registry.register_many([..., dispatch_module, ...])` |
| `python/agent/main/specs.py` | `MAIN_AGENT_SPEC.allowed_tools` 加 `"dispatch_module"`（14 tools） |
| `python/agent/main/prompt.py` | 顶部新增"教师端意图关键词路由表"（强制 dispatch_module 而非 query_knowledge）；§6/§7/§8/§10 补"必须调用 dispatch_module"指令 |
| `python/api/chat.py` | SSE `on_tool_start` 事件附带 `args` 字段（`data.input` kwargs 透传）；失败安全降级 |
| `python/eval/runner.py` | `_live_chat` 拆出 `_parse_chat_stream_events` 纯函数；dispatch_module 按 `args.intent` 映射成 `tool_chain` 元素，自身过滤掉 |
| `python/tests/test_dispatch_module.py` | 新增：5 个工具单测（input 校验 / 4 个合法 intent / payload 透传） |
| `python/tests/test_eval_runner_dispatch.py` | 新增：8 个 SSE 解析单测（含 dispatch_module 映射 / 噪音过滤 / 坏 JSON 容错） |

### SSE 协议扩展

原协议：`event: tool\ndata: {"tool": "<name>", "status": "start"|"end"}`
新协议：`event: tool\ndata: {"tool": "<name>", "status": "start"|"end", "args": {<kwargs>}}`

`args` 仅在 `status=="start"` 时附带。runner 据此把 `dispatch_module.intent` 映射成模块名（"report"/"evaluation"/...），与 eval 期望对齐。

### runner 解析逻辑

```python
if tool_name == "dispatch_module":
    intent = args.get("intent") if isinstance(args, dict) else None
    if intent:
        tools.append(str(intent))   # 把模块名映射进 tool_chain
    continue                        # dispatch_module 自身不计入（避免重复）
```

防御：`args` 缺失/格式异常时 dispatch_module 不进 tool_chain，避免裸名污染断言。

## 测试与验证

### 单测（13 个新单测 + 全量回归）

```
tests/test_dispatch_module.py .....       [ 25%]  5 passed
tests/test_eval_runner_dispatch.py ........ [ 34%]  8 passed
pytest tests/ -m "not slow"               299 passed, 4 deselected
```

### 端测（docker rebuild + live eval）

```bash
docker compose build --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim python-api
docker compose up -d python-api
curl /health → 200 healthy (model=deepseek-v4.1-flash, registry=28 tools, main_agent=14 tools)

docker exec -w /app ... python -m eval_runner.runner --set chat_intent --live \
  --case intent_04,intent_05,intent_06,intent_07
```

> **host → 容器端口代理 502**：docker desktop 转发端口 8000 时偶发 502（容器内 200 OK），临时绕过用 `docker exec` 在容器内直接跑 eval runner。

### 端测结果

```
== eval set: chat_intent (live)
[PASS] intent_04 (easy)   25653.7ms  → 已为您路由到 成绩单报告模块（report）
[PASS] intent_05 (medium) 14501.6ms → 已为您路由到 报告生成模块
[PASS] intent_06 (easy)   12516.3ms → 已为您路由到 评价寄语模块（evaluation）
[PASS] intent_07 (medium) 14241.1ms → 已为您路由到 评价寄语模块（evaluation）
== 4/4 passed | p50=14501.6ms p95=25653.7ms
   ttft_p50=10545.4ms | api_p50=14494.1ms
   by_difficulty={'easy': {'total': 2, 'passed': 2}, 'medium': {'total': 2, 'passed': 2}}
report: eval/reports/chat_intent-2026-08-18.json
```

报告已落到 `python/eval/reports/chat_intent-2026-08-18.json`（pass_rate=1.0，4/4）。

## 经验与后续

- **SSE 协议扩展最小侵入**：只动 `tool` 事件的 `data` 字典加 `args`，前端不需要感知（前端只读 `tool/status`），eval runner 据此增强解析。后续如需给其他工具加参数化断言（如 `query_knowledge.query`），同样套路。
- **路由工具 vs subagent 委派**：当前用 `dispatch_module` 单工具 + Literal 枚举，LLM 只需选 intent；改造成本最低。subagent 委派（`task(name="report_agent",...)`）适合需要"完整子 agent 执行"的场景（如教师端多科 Excel 解析），留给 Phase 4 NLU 调优时再决策。
- **host → 容器端口代理 502 是 docker desktop 转发 bug**：偶发；绕过的两个办法：
  1. `docker exec` 在容器内直接跑 runner（如本次）；
  2. 前端用 `next.config.ts` 的 `API_PROXY_TARGET` 直连容器 IP（需先 docker network inspect 取 gateway）。
- **Phase 3 试金石 #1/#5/#10 live 兑现 ✅**：与 `2026-08-18-phase3-live-eval-fulfillment.md` 一致；本次把 chat_intent 4 case 从 0/4 修到 4/4。
- **后续**：Phase 4 NLU 调优专题（chat_intent 剩余 16 个 case 全量回归 + intent 边界 case 增补）；前端 4 Page 流式事件断言补单测。
