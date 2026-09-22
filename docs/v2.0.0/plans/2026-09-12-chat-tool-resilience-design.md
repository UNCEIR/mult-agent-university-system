# Chat 工具调用韧性设计（三个缺口修复）

- 日期：2026-09-12
- 状态：已完成独立 Agent 审核，按审核意见修订
- 范围：`agent/main`、`agent/middleware/tool_hooks.py`、`api/chat.py`、工具注册/编排层、前端 chat SSE 消费
- 目标：单个工具超时/失败不再阻断整轮对话；独立工具可并行；依赖链可串行执行、按节点兜底并生成“完整/部分成功”回答

## 1. 背景与现状

当前 chat 主 Agent 使用 `deepagents + LangGraph`，模型可依次或同时发起多个工具调用。现状有三个缺口：

1. `ToolHooksMiddleware` 捕获工具异常后仍然 `raise`，会沿 LangGraph 冒泡到 `chat_stream` 的顶层异常处理。结果是天气查询失败可能让成绩查询和最终汇总都失败。
2. `chat.py` 只监听 `on_tool_start/on_tool_end`，没有处理 `on_tool_error`；`end` 事件也没有成功/失败状态，前端只能把 `end` 一律显示为成功。
3. 主 Agent 没有强制的独立工具并行/依赖链执行器。虽然 System Prompt 要求“异步多次调用”，但模型是否同时发起调用不可保证；串行链也没有显式的依赖、兜底、跳过和终止规则。

补充现状：

- `web_search` 已有 `MCP -> Tavily direct -> structured error` 兜底。
- `query_transcript` 在未登录、空结果时返回结构化结果，但底层意外异常仍可向上抛。
- `ToolHooksMiddleware` 在熔断或连续失败达到阈值后，会返回结构化 `TOOL_BLOCKED` ToolMessage；但首次真实异常仍是裸抛。
- `chat_stream` 顶层 `except Exception` 会发 SSE `error`，因此工具级异常会错误地升级成整轮请求失败。
- 前端 `ChatToolDataSchema` 目前只允许 `status=start|end`，不能丢弃新增字段，也不能识别失败状态。

## 2. 设计原则

1. **工具异常必须有结构化结果**：预期运行时异常转换为 `ToolMessage(status="error")`，不进入顶层 SSE `error`。
2. **独立子任务并行、依赖任务串行**：weather + transcript 使用 fan-out/fan-in；A 的输出作为 B 的输入时使用显式 DAG。
3. **失败按节点隔离**：任一工具超时只影响自己的依赖分支，不取消独立兄弟分支。
4. **兜底必须显式且可审计**：无兜底时终止当前分支，禁止用伪造数据继续调用下游。
5. **工具消息协议完整**：每个 `tool_call_id` 恰好对应一个 ToolMessage，避免模型协议缺失。
6. **用户只看到可行动结果**：成功部分照常回答；失败部分说明原因、是否已兜底以及用户下一步。
7. **控制流异常不能被吞**：`asyncio.CancelledError`、LangGraph control-flow/interrupt 类异常继续向上抛。

## 3. 统一工具执行契约

### 3.1 错误异常

新增 `python/tools/errors.py`：

```python
class ToolInvocationError(Exception):
    def __init__(
        self,
        code: str,
        public_message: str,
        *,
        retryable: bool = False,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message
        self.retryable = retryable
        self.cause = cause
```

错误码至少包括：

| code | 含义 | 默认重试 |
|---|---|---|
| `TOOL_TIMEOUT` | 单工具超时 | 是 |
| `TOOL_CONNECTION` | 网络/连接失败 | 是 |
| `TOOL_UPSTREAM_5XX` | 上游临时故障 | 是 |
| `TOOL_CIRCUIT_OPEN` | 熔断器已打开 | 否，转兜底 |
| `TOOL_BLOCKED` | 连续失败/策略拦截 | 否 |
| `AUTH_REQUIRED` | 未登录或权限不足 | 否 |
| `INVALID_ARGUMENT` | 参数/契约错误 | 否 |
| `NO_DATA` | 合法执行但无数据 | 否，按空结果处理 |
| `TOOL_INTERNAL` | 未分类内部异常 | 否 |

### 3.2 ToolMessage 错误内容

错误结果统一序列化为 JSON：

```json
{
  "isError": true,
  "code": "TOOL_TIMEOUT",
  "message": "实时天气查询超时",
  "retryable": true,
  "tool": "web_search",
  "fallback_used": false
}
```

不把 Python 堆栈、密钥、完整 URL、数据库异常细节直接返回给模型或用户。

### 3.3 结果状态

内部编排器使用以下状态，不要求模型理解：

```text
success | fallback_success | failed | skipped
```

## 4. 缺口一：ToolHooksMiddleware 不裸抛

### 4.1 修改文件

- `python/tools/errors.py`（新增）
- `python/agent/middleware/tool_hooks.py`
- `python/tests/test_tool_middleware.py`

### 4.2 设计

在 middleware 内增加：

```python
def _to_error_message(request, exc) -> ToolMessage: ...
def _classify_exception(exc) -> ToolInvocationError: ...
def _tool_timeout(tool_name: str) -> float: ...
```

`awrap_tool_call` 行为改为：

```text
1. 熔断/连续失败检查失败 -> 返回 TOOL_BLOCKED ToolMessage
2. await asyncio.wait_for(handler(request), timeout=policy.timeout)
3. 成功 -> 记录 success，返回原结果
4. ToolInvocationError/超时/普通 Exception
   -> 记录 failure
   -> 返回 status=error 的结构化 ToolMessage
5. asyncio.CancelledError / LangGraph control exception
   -> 记录并继续向上抛
```

同步 `wrap_tool_call` 使用同一错误分类，不重复实现。

### 4.3 关键约束

- 必须保留 `tool_call_id` 与 `name`。
- 必须调用 `_after()` 记账，不能让被转换的异常漏掉熔断/指标。
- 默认超时按工具分组配置，I/O 工具短于长任务工具；编码时集中到 `ToolExecutionPolicy`，不要散落硬编码。
- `NO_DATA` 是合法空结果，不应触发熔断失败计数。
- 连续失败阈值和 circuit breaker 继续保留，但以结构化 ToolMessage 拦截。

### 4.4 单元测试

- `handler` 抛 `TimeoutError` -> 返回 `status=error`，不抛异常。
- `handler` 抛 `ValueError` -> `INVALID_ARGUMENT` ToolMessage。
- `handler` 抛 `ToolInvocationError` -> 保留 code/retryable。
- `CancelledError` -> 原样向上抛，不能转换成 ToolMessage。
- 连续三次异常 -> 第四次返回 `TOOL_BLOCKED`，熔断与指标计数正确。
- 成功结果仍原样返回，且失败计数清零。

## 5. 缺口二：SSE 工具失败事件与终态

### 5.1 后端修改

文件：`python/api/chat.py`

新增工具 run 状态表：

```python
tool_runs: dict[str, dict[str, Any]] = {}
```

监听：

- `on_tool_start`：记录 `run_id -> {tool, started_at}`；事件保持 `status=start`，附 `run_id` 和 `args`。
- `on_tool_end`：根据 `data.output` 判断成功/失败，继续发 `status=end`，新增 `ok`、`code`、`message`、`latency_ms`、`result`。
- `on_tool_error`：作为 `on_tool_end` 的防御性回退，发 `status=end`、`ok=false`；重点是兼容前端当前枚举，不直接引入会被旧 schema 丢弃的新 status。

事件示例：

```json
{
  "event": "tool",
  "data": {
    "tool": "web_search",
    "status": "end",
    "ok": false,
    "code": "TOOL_TIMEOUT",
    "message": "实时天气查询超时",
    "retryable": true,
    "session_id": "s1",
    "run_id": "run_123",
    "latency_ms": 10020
  }
}
```

成功事件：

```json
{
  "event": "tool",
  "data": {
    "tool": "query_transcript",
    "status": "end",
    "ok": true,
    "session_id": "s1",
    "result": "命中 3 条个人成绩片段"
  }
}
```

顶层 SSE `error` 只保留给 Agent/模型/框架级不可恢复错误。工具失败必须优先形成 ToolMessage，让模型生成正常的部分成功回答。

### 5.2 前端修改

文件：

- `frontend/src/types/sse.ts`
- `frontend/src/types/index.ts`
- `frontend/src/components/AgentActivityTimeline.tsx`
- `frontend/tests/types/sse.spec.ts`
- `frontend/tests/components/AgentActivityTimeline.spec.tsx`

保持 `status: 'start' | 'end'` 不变，新增可选字段：

```ts
ok?: boolean
code?: string
message?: string
retryable?: boolean
run_id?: string
latency_ms?: number
```

`AgentActivityTimeline` 行为：

- `start`：loading + act。
- `end + ok !== false`：绿色成功成功态。
- `end + ok === false`：红色/错误态，显示用户可读 message。

### 5.3 测试

后端新增 fake agent 事件序列：

1. 两个工具 start。
2. 一个工具 end 成功。
3. 一个工具 end `ok=false`。
4. 模型生成文本。
5. `done`。

断言不出现顶层 `error`，事件顺序正确，失败 ToolMessage 的 code 被保留。

前端测试断言：

- `ok=false` 被 schema 保留，不被 zod strip。
- timeline 对失败工具显示 error 态。
- 旧的 start/end 事件仍能正常解析。

## 6. 缺口三：独立/串行工具编排器

### 6.1 设计边界

模型驱动工具调用适合开放式任务，但不能保证依赖链的执行顺序和失败恢复。新增确定性编排层，供 skill/subagent/后端工作流复用；主 Agent 的模型调用仍由 middleware 获得单工具韧性。

新增 `python/agent/main/tool_plan.py`，不在主 Agent 暴露任意动态执行器，避免模型绕过 allowlist。

### 6.2 数据结构

```python
@dataclass(frozen=True)
class ToolStep:
    step_id: str
    tool_name: str
    build_args: Callable[[dict[str, ToolStepResult]], dict[str, Any]]
    depends_on: tuple[str, ...] = ()
    required: bool = True
    max_retries: int = 1
    timeout_s: float = 20.0
    fallback: ToolFallback | None = None


@dataclass
class ToolStepResult:
    step_id: str
    tool_name: str
    status: Literal["success", "fallback_success", "failed", "skipped"]
    data: Any = None
    error: ToolInvocationError | None = None
    attempts: int = 0
    provider: str | None = None
```

### 6.3 并行执行

用于天气 + 成绩这类互不依赖任务：

```text
for step in steps:
    validate_tool_allowed(step.tool_name)

await asyncio.gather(
    *[execute_one(step) for step in steps],
)
```

`execute_one()` 必须：

- 使用统一策略和 middleware 相同的错误分类。
- 单次 `asyncio.wait_for`。
- 超时/临时错误最多重试 `max_retries`。
- 不把异常抛给 `gather`，而是返回 `failed` 结果。
- 只重试只读且幂等的工具。
- 任一失败不影响其他 `Task`。

### 6.4 串行执行

用于严格依赖链：

```text
A -> B -> C
```

伪代码：

```python
outputs = {}
for step in topological_steps:
    if not dependencies_ok(step, outputs):
        outputs[step.step_id] = skipped_result(step)
        continue

    args = step.build_args(outputs)
    result = await execute_one(step, args)

    if result.status == "failed" and step.fallback:
        result = await step.fallback.run(outputs, result)

    if result.status in {"success", "fallback_success"}:
        result = validate_schema(result, step.output_schema)
        if not result.ok:
            result = failed_result(result.error)

    outputs[step.step_id] = result
```

规则：

- A 失败且无可用 fallback：B/C 置为 `skipped`，不得用缺失/伪造字段调用 B。
- A 失败但有同 schema 的 fallback：可继续 B，状态标为 `fallback_success`。
- `AUTH_REQUIRED`、`INVALID_ARGUMENT` 不重试。
- `NO_DATA` 可终止当前必要分支，但不能触发熔断失败计数。
- 每个步骤结果都保留 attempts、provider、fallback_used，供最终回答和观测使用。
- 长链可把 step 状态写入 Agent checkpoint/state，重试时从失败步骤恢复，而不是重跑整个链。

### 6.5 串行链示例

`查询我的成绩 -> 根据成绩推荐课程`：

```text
query_transcript
  success -> validate course/grade schema -> recommend_courses
  timeout/connection error -> get_academic_snapshot fallback
  fallback success -> recommend_courses
  no fallback -> skipped(recommend_courses) -> partial failure answer
```

天气 + 成绩：

```text
web_search[strategy=weather]  ─┐
                               ├─ parallel
query_transcript              ─┘
        ↓
LLM 汇总完整或部分成功结果
```

### 6.6 测试

- `gather` 两个工具，一个 sleep 超时，另一个正常完成；总耗时不等于两者串行时间。
- 一个工具失败不会取消另一个工具任务。
- 串行 A 成功后 B 收到 A 的结构化字段。
- A 失败无 fallback -> B 不执行，最终返回 `skipped`。
- A 超时 fallback 成功 -> B 正常执行，状态为 `fallback_success`。
- 不允许的 tool name 在调用前拒绝。
- 重试次数、总 deadline、熔断状态符合策略。
- 取消整轮时 `CancelledError` 不被吞。

## 7. 最终回答合成

编排结束后，将以下结构化摘要交给 LLM：

```json
{
  "overall_status": "partial_success",
  "steps": [
    {"step_id": "weather", "status": "failed", "code": "TOOL_TIMEOUT"},
    {"step_id": "transcript", "status": "success"}
  ]
}
```

LLM 合成规则：

1. `success/fallback_success` 的部分正常回答。
2. `failed` 的部分明确说明不可用，不编造数据。
3. `skipped` 的部分说明前置条件没满足。
4. 只有全部成功才表现成完整成功。
5. 有部分成功时，整体不是顶层 SSE `error`，而是正常 assistant 文本。
6. fallback 对用户结果无影响时可简写；影响可信度/时效性时必须说明。
7. 不暴露内部异常堆栈、熔断状态和工具实现细节。

回答示例：

```text
深圳今天有雷阵雨，26~31℃，数据来源……。

你的成绩查询本次超时，系统已尝试备用结构化快照但未取得结果，因此无法生成基于成绩的后续推荐。天气部分不受影响，你可以稍后重试成绩查询。
```

## 8. 可观测性

工具执行统一记录：

- `tool_name`
- `step_id`
- `status`
- `code`
- `attempts`
- `latency_ms`
- `fallback_used`
- `cancelled`
- `circuit_state`

指标建议：

- `tool_calls_total{tool,status,code}`
- `tool_duration_seconds{tool}`
- `tool_fallback_total{tool,from,to}`
- `tool_chain_partial_total{workflow}`
- `tool_chain_skipped_total{workflow,step}`

审计日志禁止写入完整个人信息、密钥和工具原始输入。

## 9. 实施顺序

1. **阶段 A：工具错误隔离**
   - 新增错误契约。
   - 修改 middleware。
   - 补单元测试。
2. **阶段 B：SSE 与前端状态**
   - 处理 `on_tool_end/on_tool_error`，补 `ok/code/message/latency_ms`。
   - 更新 zod schema 和 timeline。
   - 补流式集成测试。
3. **阶段 C：编排器**
   - 新增 `ToolPlanExecutor`。
   - 支持并行、串行、fallback、schema 校验、deadline。
   - 先接内部工作流，不暴露给模型任意调用。
4. **阶段 D：接入 chat**
   - 独立多意图：走并行计划器或确认模型可生成多 tool_calls。
   - 强依赖链：路由到确定性 workflow/subagent。
   - 更新 System Prompt，只描述调用意图，不要求模型实现容错细节。

## 10. 验收标准

- 工具抛异常时，主 Agent 仍能收到错误 ToolMessage，并可继续调用其他独立工具。
- 用户取消/断连时，取消信号正常传播，不被错误包装。
- 两个独立工具的执行时间接近并行而不是串行。
- 一个工具失败不会触发另一个工具取消，也不会直接产生顶层 SSE `error`。
- 串行链中前置失败不会用空值或伪造值调用下游。
- 串行链有合法 fallback 时可以继续，并在结果中记录来源。
- 前端能区分工具成功与失败，仍兼容旧 start/end 事件。
- `python -m pytest tests/ -m "not slow"`、`npm test`、`npm run lint`、`npm run build` 全部通过。

## 11. 待审核重点

1. 对 deepagents/LangGraph 的 control-flow exception 过滤是否完整。
2. `ToolHooksMiddleware` 返回 error ToolMessage 后，deepagents 是否一定继续下一轮模型调用。
3. 并行工具调用由 LangGraph 自身调度时，是否与 `asyncio.wait_for` 和全局 deadline 有冲突。
4. SSE `status=end + ok=false` 的兼容性是否优于新增 `status=error`。
5. 串行编排器是否应先支持内部 workflow，而不是暴露给主 Agent。
6. ToolMessage error status 是否会被某些模型 provider 拒绝，是否需要统一 `status=success` + `isError=true` 的内部折中。

## 12. 独立 Agent 审核结论与强制修订

审核日期：2026-09-12。审核结论：原设计方向正确，但不能直接编码；以下内容为最终执行口径。如与上文冲突，以本节和后续修订条款为准。

### 12.1 P0：先修复 middleware 未实际挂载

审核发现 `python/agent/main/factory.py` 先把 `ToolHooksMiddleware` append 到 `middleware`，后续又直接覆盖 `middleware = [summarization, SummarizationToolMiddleware(...)]`。这会导致所有 ToolHooks 修复即使完成也不会生效。

实施前必须先修改 `factory.py`，保留已有 middleware：

```python
middleware = [
    *middleware,
    summarization,
    SummarizationToolMiddleware(summarization),
]
```

并增加测试，断言传给 `create_deep_agent` 的 middleware 同时包含 `ToolHooksMiddleware` 和 summarization middleware，且没有重复实例。文件范围必须加入 `python/agent/main/factory.py`。

### 12.2 P0：SSE 不能以 on_tool_end.data.output 作为唯一失败来源

当前 `langchain 1.3.14 + v1 events + ToolNode` 的实际事件形态并非稳定的一对一：

- 正常工具：`on_tool_end.output` 通常是 `ToolMessage`。
- 工具异常被 middleware 转换为错误 ToolMessage：可能出现 `on_tool_end`，但 `output=None`，且不一定有 `on_tool_error`。
- middleware 直接返回 `TOOL_BLOCKED`：可能没有 `on_tool_start/on_tool_end`。
- 完整错误 ToolMessage 更可靠的来源是 `on_chain_end(name="tools")` 的 `data.output["messages"]`。

因此 SSE 设计改为以 ToolNode 节点生命周期为主、细粒度工具事件为辅：

1. `on_chain_start(name="tools")`：
   - 从输入中最后一个 `AIMessage.tool_calls` 提取 `tool_call_id/tool/args`。
   - 为每个调用发 `status=start`。
2. `on_chain_end(name="tools")`：
   - 从 `data.output["messages"]` 按 `tool_call_id` 匹配最终 `ToolMessage`。
   - 按 ToolMessage 的 `status/content` 发 `status=end + ok`。
   - 未匹配到结果的调用生成合成失败 end，避免 UI 永久 loading。
3. `on_chain_error(name="tools")`：
   - 为所有仍未结束的调用合成 `status=end + ok=false`。
4. `on_tool_start/on_tool_end`：
   - 只用于补充真实 latency/run_id/result 摘要；
   - 不作为错误状态的唯一来源。
5. 关联主键改为 `tool_call_id`，`run_id` 仅作诊断字段。

### 12.3 P0：前端必须按 tool_call_id 匹配并发工具

当前 `frontend/src/app/(main)/chat/page.tsx` 使用 `last.tools.find(t => t.name === tool)` 匹配事件。同名工具并行时会错合并 start/end。

强制修改：

- `ToolActivity` 增加 `tool_call_id: string`、`ok?: boolean`、`code?: string`、`message?: string`、`latency_ms?: number`。
- Chat SSE schema 增加 `tool_call_id`，保留原有 `status: start|end`，新增可选 `ok/code/message/retryable/run_id/latency_ms`。
- 前端按 `tool_call_id` 查找和更新，不使用 tool name 作为关联键。
- 兼容旧事件：没有 `tool_call_id` 时才回退到 `run_id`，最后才回退到 name；重复 end 必须幂等。

### 12.4 P0：补齐 ToolPlanExecutor 契约

原设计中的 `ToolFallback`、`output_schema`、`result.ok`、`ToolPlan`、依赖排序、deadline 均未完整定义。修订后接口如下：

```python
@dataclass(frozen=True)
class ToolFallback:
    tool_name: str
    build_args: Callable[[dict[str, ToolStepResult]], dict[str, Any]]
    timeout_s: float
    max_retries: int = 0
    output_schema: type[BaseModel] | None = None


@dataclass(frozen=True)
class ToolStep:
    step_id: str
    tool_name: str
    build_args: Callable[[dict[str, ToolStepResult]], dict[str, Any]]
    depends_on: tuple[str, ...] = ()
    required: bool = True
    max_retries: int = 1
    timeout_s: float = 20.0
    output_schema: type[BaseModel] | None = None
    fallback: ToolFallback | None = None


@dataclass(frozen=True)
class ToolPlan:
    plan_id: str
    steps: tuple[ToolStep, ...]
    allowed_tools: frozenset[str]
    deadline_s: float


@dataclass
class ToolStepResult:
    step_id: str
    tool_name: str
    status: Literal["success", "fallback_success", "no_data", "failed", "skipped"]
    data: Any = None
    error: ToolInvocationError | None = None
    attempts: int = 0
    provider: str | None = None
    fallback_used: bool = False
```

执行前校验：

- `step_id` 唯一。
- 所有依赖存在。
- 依赖图无环。
- `tool_name` 必须在 plan 的显式 `allowed_tools` 中。
- fallback tool 也必须通过 allowlist。
- `build_args` 必须可调用。
- 总 deadline 必须覆盖主调用、重试和 fallback。

`NO_DATA` 状态独立为 `no_data`。若下游允许空值，则继续；若下游要求数据，则按依赖未满足跳过，不按系统故障计入熔断。

`required=False` 的步骤失败不阻断后续非依赖步骤；`required=True` 的步骤失败会使其依赖分支变为 `skipped`，独立兄弟分支继续执行。

### 12.5 异常转换必须排除控制流异常

必须原样重抛：

- `asyncio.CancelledError`
- `langgraph.errors.GraphBubbleUp`
- `GraphInterrupt`
- `ParentCommand`
- `GraphDrained`
- 进入 middleware 时的 `NodeCancelledError`、`NodeTimeoutError`

禁止 `except BaseException`。推荐：

```python
except (asyncio.CancelledError, GraphBubbleUp):
    raise
except Exception as exc:
    return self._to_error_message(request, self._classify_exception(exc))
```

LangChain/OpenAI 兼容层不会把 ToolMessage 的 `status=error` 当作请求参数；错误 ToolMessage 默认会回到模型节点，使模型可以继续下一轮。但必须用真实 ToolNode/fake model 做集成测试验证，不能只靠单元测试。

### 12.6 wait_for、并发和 MCP 取消

ToolNode 已通过 `asyncio.gather` 并发执行同一 AIMessage 的多个工具，单工具 `wait_for` 通常不会取消兄弟任务。但必须补充：

- 每个工具的超时、重试、fallback 共享同一个总 deadline；禁止 `timeout_s × max_retries` 无限放大。
- `wait_for` 无法停止同步阻塞代码；CPU/同步 I/O 工具必须放到线程/进程或另设限制。
- MCP 共享 session 被取消后必须执行清理/重连，避免一次 timeout 污染后续调用。
- middleware 顺序固定。若重试 middleware 放在 ToolHooks 外层，它看到的是 ToolMessage 而不是异常，不会执行异常重试；重试必须与错误分类使用同一层或明确的先后顺序。

### 12.7 allowlist、熔断和错误计数修订

`ToolRegistry.register()` 会把所有注册工具自动加入 `_allowlist`，因此现有 `is_allowed()` 不能作为编排器权限门。必须使用：

- plan 级显式 `allowed_tools`；
- 或 agent spec 的 `allowed_tools`；
- `mark_internal()` 工具默认拒绝；
- fallback 工具同样走 allowlist。

熔断失败计数只记录真正的运行时故障：`TOOL_TIMEOUT`、`TOOL_CONNECTION`、`TOOL_UPSTREAM_5XX`、`TOOL_INTERNAL`。

以下不增加全局熔断失败次数：

- `NO_DATA`
- `AUTH_REQUIRED`
- `INVALID_ARGUMENT`
- `TOOL_BLOCKED`
- 用户主动取消

此外，当前 breaker 和连续失败计数是进程级共享状态。编码前必须决定按工具全局隔离还是按 `(tool, tenant/user)` 隔离，避免一个用户的权限错误阻断其他用户。

### 12.8 PII 与 SSE 参数脱敏

当前 `chat.py` 会把工具原始参数写入 SSE 和 EventBuffer，可能包含 Prompt、个人信息或凭据。修订要求：

- SSE 默认不返回原始 args，只允许白名单字段（如 query 长度、非敏感过滤条件）。
- 所有参数摘要必须限制长度并脱敏。
- EventBuffer、结构化日志、metrics label 均不得包含原始 Prompt、成绩、学号、token、URL 查询参数。
- 顶层 SSE `error` 不得直接返回 `str(exc)`；只返回稳定错误码和公开 message，细节只进受控日志。

### 12.9 必补测试

在原有测试之外强制增加：

- 真实 ToolNode/fake model 集成测试：工具 A 返回错误 ToolMessage 后，模型继续调用工具 B 并生成最终回答。
- `GraphInterrupt`、`ParentCommand`、`GraphDrained`、`CancelledError` 原样传播测试。
- 真实 `asyncio.wait_for` 超时、外层取消、同步阻塞工具和 MCP 取消清理测试。
- middleware 短路/熔断时 SSE 仍能从 tools 节点生成完整 start/end。
- 同名工具并行、事件乱序、重复 end、缺失 end 测试。
- ToolPlanExecutor 的重复 step、缺失依赖、环、未授权工具、fallback 失败、schema 失败、`no_data`、非幂等工具不重试、总 deadline 测试。
- SSE、EventBuffer、日志、metrics 均不含原始参数、密钥和个人信息。

### 12.10 最终实施顺序修订

1. **A0**：修复 `factory.py` middleware 覆盖，补挂载断言测试。
2. **A**：工具异常契约、控制流异常重抛、熔断计数策略、统一 deadline。
3. **B**：SSE 改为基于 `tools` 节点生命周期 + `tool_call_id` 关联；原 `on_tool_*` 仅补充 latency/result。
4. **B1**：前端 schema、chat 页面和 timeline 支持失败状态及并发匹配。
5. **C**：补齐并测试 `ToolPlanExecutor`，先接内部 workflow/subagent，不暴露任意动态执行器给模型。
6. **D**：接入 chat 多意图与串行链，更新 System Prompt 和验收测试。

### 12.11 修订后的 Go/No-Go

当前文档经独立审核后的结论是：

- **可以进入编码的前提**：先接受 12.1～12.9 的强制修订。
- **不得直接实施原版本**：尤其是 factory 覆盖、SSE 事件来源和 executor 契约未补齐会造成“看似修了但实际不生效”或并发事件串线。
- **建议先提交 A0 + A 的最小 PR**，通过 middleware 挂载和异常隔离集成测试后，再进入 SSE 与编排器阶段。
