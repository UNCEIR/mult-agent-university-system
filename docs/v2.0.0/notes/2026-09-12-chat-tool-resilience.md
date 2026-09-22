# Chat 工具调用韧性：三缺口修复复盘

## 背景与问题

- 本轮要解决的问题：让 chat 主 Agent 在单个工具超时/失败时继续执行独立工具，并把工具失败与 Agent 级失败分开；同时为串行依赖链提供确定性执行能力。
- 触发原因或用户诉求：
  1. `ToolHooksMiddleware` 捕获异常后仍会 `raise`，单个工具失败可能终止整轮对话。
  2. `chat_stream` 只监听工具 start/end，且 `on_tool_end.data.output` 不能可靠表示业务成功/失败。
  3. 模型驱动的工具调用没有显式并行/串行编排器，依赖链缺少兜底、跳过和终止规则。
- 影响范围：
  - 后端：`agent/main/factory.py`、`agent/middleware/tool_hooks.py`、`api/chat.py`、工具错误契约、内部工具计划。
  - 前端：chat SSE 类型、chat 页面工具匹配、AgentActivityTimeline、相关测试。
  - 用户侧：天气 + 成绩等多意图请求、成绩 -> 推荐等依赖链、SSE 工具进度可视化。

设计入口：`docs/v2.0.0/plans/2026-09-12-chat-tool-resilience-design.md`。该设计经过独立 Agent 审核，核心 P0 修订已纳入实现。

## 总体架构方案

- 涉及模块：
  - `python/tools/errors.py`：统一工具错误码、重试/熔断分类和每工具执行策略。
  - `python/agent/middleware/tool_hooks.py`：工具横切容错、超时、重试、失败 ToolMessage 和记账。
  - `python/services/chat_tool_events.py`：把 LangGraph events 转为前端 SSE 工具事件。
  - `python/agent/main/tool_plan.py`：确定性 DAG 工具编排器。
  - `frontend/src/lib/toolActivity.ts`：并发工具事件按 `tool_call_id` 合并。
- 调用链：
  1. `factory` 同时挂载 `ToolHooksMiddleware` 与 summarization middleware。
  2. 工具异常被 middleware 转成 `ToolMessage(status="error")`，模型收到后仍可调用独立工具。
  3. SSE 以 `tools` 节点输入/最终 messages 为权威来源，按 `tool_call_id` 发 start/end。
  4. 前端按 `tool_call_id` 更新调用，失败显示 `ok=false`，不再按工具名错配并发调用。
  5. 强依赖链由 `ToolPlanExecutor` 执行，按波次并行、按依赖串行、失败时跳过依赖分支。
- 关键设计取舍：
  - 保留 SSE `status=start|end`，新增 `ok/code/message/...`，避免旧前端 schema 丢弃新事件。
  - `ToolMessage(status="error")` 作为模型可见失败结果，顶层 SSE `error` 只保留给 Agent/模型/框架级失败。
  - `ToolRegistry.is_allowed()` 不适合作为编排权限门；`ToolPlan` 使用显式 `allowed_tools`，并拒绝 internal 工具。
  - 超出稳定公共错误信息的内容只进受控日志，不直接进入 SSE 或模型上下文。

## 细节实现

- `python/agent/main/factory.py`
  - 修复原列表覆盖问题：`middleware = [*middleware, summarization, SummarizationToolMiddleware(summarization)]`。
  - 保证 `ToolHooksMiddleware` 真实传给 `create_deep_agent`。
- `python/tools/errors.py`
  - 新增 `TOOL_TIMEOUT / TOOL_CONNECTION / AUTH_REQUIRED / NO_DATA / TOOL_INTERNAL` 等错误码。
  - 新增 `ToolExecutionPolicy`，集中配置工具超时与重试。
  - 明确哪些错误计入熔断失败，哪些属于非故障状态。
- `python/agent/middleware/tool_hooks.py`
  - 使用 `asyncio.wait_for` 包装异步工具调用，并让重试共享同一 deadline。
  - 预期异常统一返回结构化错误 ToolMessage，保留 `tool_call_id/name/status`。
  - 重抛 `CancelledError`、`GraphBubbleUp` 及 LangGraph 控制流异常。
  - auth/invalid/NO_DATA/blocked 不增加全局熔断失败次数。
  - 延迟导入 `tools.errors`，避免 `tool_hooks -> tools.__init__ -> agent.main -> factory` 循环导入。
- `python/services/chat_tool_events.py`
  - 以 `on_chain_start/end/error(name="tools")` 为主，`on_tool_start/end/error` 只补充 run_id、latency 和摘要。
  - 从工具调用 ID 关联 start/end，并在缺失终态时合成 `TOOL_UNFINISHED/TOOL_NODE_ERROR`。
  - 对 args 做白名单摘要，query 等敏感字段只保留长度。
- `python/api/chat.py`
  - 接入 `ChatToolEventTracker`，工具失败不再直接升级为顶层 SSE error。
  - 顶层异常只发送稳定 code/公开 message，不直接回传原始 exception。
- `python/agent/main/tool_plan.py`
  - 新增 ToolPlan/ToolStep/ToolFallback/ToolStepResult。
  - 支持无依赖并行波次和依赖串行；前置失败时向下游置 `skipped`。
  - 支持 fallback、输出 schema、NO_DATA、显式 allowlist、重复/缺失依赖/环检测和总 deadline。
- 前端
  - `ChatToolDataSchema`/`ChatToolData` 新增 `tool_call_id/run_id/ok/code/message/retryable/latency_ms`。
  - `upsertToolActivity` 以 `tool_call_id` 为主键；旧事件才回退 run_id/name。
  - `AgentActivityTimeline` 增加红色 error 终态。
- 主 Agent prompt
  - 增加“多工具容错与部分成功”约束：独立任务尽量并发、单工具 `isError` 不终止整轮、依赖链不得用伪造值继续、成功部分正常回答。

## Debug 结论

- 根因：
  1. middleware 实例创建后，后续 `middleware = [...]` 覆盖了 include ToolHooks 的列表，导致横切钩子在运行时根本未执行。
  2. `on_tool_end` 只是 callback span 结束，不等于业务成功；错误 ToolMessage 可能在 `tools` 节点输出中，或由 middleware 短路而不产生逐工具事件。
  3. 前端按工具名匹配 start/end，同名工具并行时会错误合并。
  4. 原编排设计缺少完整接口和执行规则，无法可靠表达依赖跳过、fallback 和全局 deadline。
- 证据链：
  - `factory.py` 原 L90-99 append ToolHooks，L122 覆盖列表，最终 `create_deep_agent(middleware=middleware)` 看不到 ToolHooks。
  - 独立审核实测：middleware 直接返回 ToolMessage 时可能无 `on_tool_error`，`on_tool_end.output` 可能为 `None`；`tools` 节点最终 messages 更完整。
  - 旧 chat 页面以 `t.name === tool` 查找，无法区分两个同名并发调用。
  - 原 ToolPlanExecutor 草案存在 ToolFallback/output_schema/result.ok/ToolPlan 等未定义项。
- 解决方式：
  - 先修复 middleware 装配，再做异常 ToolMessage 转换。
  - SSE 改用 tools 节点生命周期为主，tool_call_id 关联，frontend 同步按 ID 合并。
  - ToolPlanExecutor 补齐契约与 allowlist/deadline/schema/fallback 规则。
  - 使用真实 ToolNode + fake tool-calling model 验证“工具 A 失败后模型仍继续并生成最终回答”。

## 测试与验证

- 已执行：
  - 后端定向测试：`python -m pytest tests/test_chat_intent_prompt.py tests/test_chat_tool_events.py tests/test_tool_middleware.py tests/test_tool_plan.py tests/test_tool_resilience_integration.py tests/test_chat_stream_tool_events.py tests/test_agent_factory.py -q`。
  - 后端非 slow suite（按用户要求跳过依赖缺失 `tests.fake_mcp_server` 的 `test_main_agent_plugins.py`、`test_mcp_client.py`）。
  - 前端测试：`npm test`。
  - 前端静态检查：`npm run lint`。
  - 前端生产构建：`npm run build`。
- 结果：
  - 后端定向测试：63 passed。
  - 后端非 slow suite：510 passed，1 failed，4 deselected，4 warnings。
  - 唯一失败：`tests/test_api_envelope.py::test_health_probe_not_enveloped`，失败原因是 `/health` 返回了统一信封；该文件本轮未改动，属于既有/独立问题。
  - 前端：21 个测试文件、161 tests passed；lint 通过；Next.js production build 通过。
  - `git diff --check` 无 whitespace error，仅提示仓库 LF/CRLF 转换。
- 未执行及原因：
  - 未执行依赖真实 LLM / 外部搜索 / Milvus / MySQL 的 live 验证，避免真实额度和外部依赖不确定性。
  - 未修复 `/health` 信封失败，保持本轮范围聚焦 chat 工具韧性。

## 经验与后续

- 本轮经验：
  - middleware 的“实例已创建”不等于“已挂载”；工厂装配必须有断言测试。
  - 工具回调事件只适合过程观测，不应作为业务成功/失败唯一真相来源。
  - 多工具事件必须以 `tool_call_id` 关联，工具名不是唯一键。
  - 容错改造必须先区分控制流异常与普通业务异常，不能统一吞掉。
  - 编排器的 allowlist 必须显式且默认拒绝，不能依赖自动注册形成的宽松集合。
- 后续建议：
  - 单独修复 `/health` 未封装断言，确认 envelope middleware 的豁免规则。
  - 为 `ToolPlanExecutor` 接入一个真实内部 workflow（例如成绩 -> 推荐）并增加 live/contract 测试。
  - 若前端后续展示工具参数，只使用后端已脱敏的参数摘要，禁止直接回传原始 args。
  - 观察 MCP 共享 session 在 wait_for 取消后的重连行为，必要时补取消清理测试。
