# 面试 STAR 故事集（v2.0.0 重写，8 个故事）

> v1 时代故事聚焦 v1 supervisor / ReAct 工具 / 硬约束。v2 已升级到 **main_agent（deepagents 框架）路由 + 4 业务模块 + 5 MCP 工具**。8 个故事按 v2 业务范围（路 0~7 + Phase 3 live eval 兑现）真实沉淀，每条含「场景 / 任务 / 行动 / 结果」STAR + 60 秒口播 + 3 分钟展开 + 可追问点。

## 故事 1：main_agent 路由教师端意图（路 1 复盘）

### STAR

**S（场景）**：v2 main_agent 升级到 deepagents 框架后，**教师端 4 个 chat_intent case 全部失败**——LLM 识别到"老师想出报告/写评语"这类意图时，**没调 dispatch_module**，反而去查知识库或干脆停住。

**T（任务）**：让 LLM 在看到"成绩单 / 评语 / 期末报告"等教师端关键词时，**必调 dispatch_module(intent=...)** 而非退回 query_knowledge。

**A（行动）**（4 步）：
1. 在 `python/agent/main/prompt.py` 顶部加"教师端意图关键词路由表"（4 模块 + 关键词）
2. 显式禁止"把成绩单/评语/期末报告当知识库问答"
3. 把 `dispatch_module` 改**必含**在 `MAIN_AGENT_SPEC.allowed_tools`
4. 增 4 个边界 case（image_generate / ppt / 多轮上下文 / 跨意图）

**R（结果）**：`eval/reports/chat_intent-2026-08-18.json` 4/4 通过；`tests/test_chat_intent_prompt.py` 20 个契约测试锁死关键词 + 模块映射。

### 60 秒口播

> 升级到 deepagents 后发现教师端 4 个 chat_intent 案例全失败——LLM 识别到"出报告"反而去查知识库。我重写了 main_agent 的 system prompt，顶部加教师端意图关键词路由表，显式禁止退回 query_knowledge，并新增 dispatch_module 工具。修复后 4/4 通过，加 20 个 prompt 契约测试锁死关键词。

### 3 分钟展开

v1 时代 supervisor 只管推荐，**没**报告/评价的意图识别。v2 main_agent 升级 deepagents 后，4 个业务模块（推荐/报告/评价/PPT）需要 main_agent 路由。失败根因是 LLM 不知道有 `dispatch_module` 工具可用。**修法不在工具端，在 prompt 端**——让 LLM 知道"教师端关键词必调 dispatch_module"。然后用 `tests/test_chat_intent_prompt.py` 的 20 个契约测试锁死关键词 + 模块映射，防止后续改动误删关键段落。

### 可追问点

- Q7（main_agent system prompt 怎么写）→ 关键词路由表结构
- Q9（4 个失败 case 怎么修）→ 路 1 复盘笔记 + prompt diff
- Q11（意图路由的关键词冲突怎么解决）→ intent_20 hybrid 设计

---

## 故事 2：评价反幻觉五层直接管线（决策 5 修订 + 评估 6/6）

### STAR

**S（场景）**：教师端用 LLM 给学生写评语，**幻觉风险高**——LLM 容易"自造"学生没选的科目、编造分数。

**T（任务）**：设计一个**直接管线**（不用 ReAct），把"评语引用数值必须来自学生真实成绩单"作为硬约束拦截。

**A（行动）**（5 层反幻觉）：
1. **快照**：拉学生成绩单 + 学籍（MySQL）
2. **雷达方案**：5 维提案（3 维固定 gpa/credit/balanced + 2 维 LLM）
3. **LLM 评语**：按 comment_type 4 种驱动
4. **反幻觉核验**：评语中引用的数值必须能在 snapshot 里找到（reference.assertion 拦截）
5. **落库**：evaluation_records

**R（结果）**：`eval/reports/evaluation_comment_live-2026-08-17.json` 6/6 通过；学生 3123003252 真实成绩单（71 门课 / 144.5 学分 / 加权均分 85.85）；6 个 case 评语均引用真实数值；tokens 110K（5 medium + 1 easy case）。

### 60 秒口播

> 教师给 LLM 写评语会幻觉——我设计五层直接管线：快照学生成绩、生成雷达方案、LLM 写评语、反幻觉核验评语引用的数值必须来自快照、最后落库。6 个真实案例全过，学生 71 门课评语准确引用，无幻觉。110K tokens 消耗，p50 67 秒。

### 3 分钟展开

**为什么不用 ReAct**？ReAct 让 LLM 决定调用顺序，但幻觉防控**必须**确定顺序：先快照再写评语最后核验。直接管线（决策 5 修订）把每层做成一个 `@tool` 暴露给 main_agent，反幻觉闸做在 LLM 评语后——引用任何 snapshot 里不存在的数值会被拦截。6 个 live case 全过，关键证据是评语里能直接看到"71 门课程 / 144.5 学分 / 85.85"。

### 可追问点

- Q19（评价反幻觉怎么实现）→ 5 层管线 + reference.assertion
- Q20（加权公式怎么定）→ 0.3×display + 0.7×exam + bonus
- Q21（4 种 comment_type 差别）→ 4 种 prompt 模板

---

## 故事 3：SSE 续传协议（路 2 升级）

### STAR

**S（场景）**：v1 时代 SSE 流是**单向无续传**——客户端断网 / docker 转发层 502 / LLM 长时间生成时，前端必须从头重新发起请求并接受重复生成代价。

**T（任务）**：让 SSE 具备**断点续传**能力——客户端断线后用 `Last-Event-ID` header 触发服务端回放缺失事件，指数退避重连最多 3 次。

**A（行动）**（3 个组件）：
1. **后端 `EventBuffer`**（`python/services/sse_event_buffer.py`）：Redis `INCR` 全局自增 + `LPUSH+LTRIM` 环形缓冲（max 100 条 + TTL 30min）
2. **后端 SSE 协议升级**（4 个端点 `chat.py` / `recommend.py` / `evaluation.py` / `report.py`）：每条事件带 `id:` 字段；读 `Last-Event-ID` header 调 `replay_from()`
3. **前端 `consumeSSEWithRetry`**（`frontend/src/lib/sse.ts`）：指数退避 500ms→1s→2s（max 3），自动 `Last-Event-ID` header 透传

**R（结果）**：16 个 EventBuffer 单测（跨实例 INCR 单调 + Redis 不可用降级 + append 失败回退）；11 个前端 retry 单测（首次成功不重试 + 网络断开后重连带 Last-Event-ID + abort 后不重试）；容器内端到端验证：第一次 chat/stream 输出 53 events，重连 `Last-Event-ID=53` 后从 id=54 续传。

### 60 秒口播

> v1 SSE 单向无续传——客户端断网得从头再来。我加 EventBuffer 环形缓冲 + 每条事件 id 字段 + 客户端指数退避重连。重连时 Last-Event-ID header 触发服务端回放缺失事件。容器实测：53 个事件后断线重连，从 54 续传。16 个后端单测 + 11 个前端单测覆盖降级 / abort / abort 后不重试。

### 3 分钟展开

**最关键设计**：用 Redis `INCR` 而不是进程内计数器——保证跨进程 / 跨重启单调递增（多实例部署不冲突）。**Redis 不可用降级为本地 counter**——同进程内仍单调，跨实例不保证但有降级语义。客户端 retry 关键：**abort 后不重试**（用户主动取消 vs 网络断线要区分）。

### 可追问点

- Q23（SSE 中断后能续传吗）→ EventBuffer 16 单测 + 客户端 11 单测
- Q25（前端流式输出卡顿怎么优化）→ rAF 节流 + 取消按钮
- Q28（docker host→container 转发为什么 502）→ 路 5 修复

---

## 故事 4：dispatch_module 工具设计（路 1 + 路 3）

### STAR

**S（场景）**：main_agent 需要路由到 4 个**独立深度交互场景**（PPT 生成、图片生成、报告生成、评价寄语）——这些不适合嵌入对话流，需要引导用户到独立页面。

**T（任务）**：设计一个**通用路由工具** `dispatch_module`，4 个 intent（report/evaluation/ppt/image_generate）走统一接口。

**A（行动）**（3 步）：
1. **TypeScript Literal 枚举**（`frontend/src/components/system/dispatch_module.ts`）：`intent: Literal["report","evaluation","ppt","image_generate"]`——schema 漂移零容忍
2. **Python 对应实装**（`python/tools/system/dispatch_module.py`）：返回 JSON `{module, hint, payload}`
3. **MAIN_AGENT_SPEC.allowed_tools 必含**（`python/agent/main/specs.py:40-55`）—— 路 1 修复关键

**R（结果）**：4/4 教师端 chat_intent case 通过（`chat_intent-2026-08-18.json`）；20 个 prompt 契约测试（`test_chat_intent_prompt.py::test_main_agent_routing_module_values_match_intent_enum` 锁死 4 个 intent Literal 匹配）；**Literal 枚举 ≥ 4**（新增 intent 需同步改 prompt + 路由表 + 文档）。

### 60 秒口播

> main_agent 路由 4 个深度交互场景需要通用接口。我设计 dispatch_module 工具，4 个 intent 走 Literal 枚举，TypeScript + Python 双向校验。MAIN_AGENT_SPEC 必含，4/4 chat_intent 通过，20 个 prompt 契约测试锁死枚举匹配。

### 3 分钟展开

**为什么用 Literal 枚举而不是 string**？TypeScript + Python 双向 schema 校验（路 3 zod 也覆盖），**新加 intent 不被默默接受**——必须改所有相关位置（prompt + 路由表 + spec + 文档）。这是 OpenAPI / GraphQL enum 的同款思路，但用纯 TypeScript + Python 字面量零依赖。

### 可追问点

- Q8（dispatch_module 4 intent 怎么设计）→ Literal 枚举 + zod 校验
- Q9（教师端 4 失败 case 怎么修）→ dispatch_module 加入 allowed_tools
- Q11（意图路由关键词冲突）→ intent_20 hybrid 设计

---

## 故事 5：zod schema 统一错误反馈层（路 3）

### STAR

**S（场景）**：前端 3 套散落错误反馈——`message.error`（antd 静态 API，警告）/ `<Text type="danger">`（内联）/ StreamView 红 panel（流式）—— 6 个 page 各自写 try/catch + `.catch(() => {})`。

**T（任务）**：3 套统一收敛到 2 套（`useNotify().toast.*` 短操作 + `useNotify().inline.*` 长操作）+ zod schema 给 SSE 事件做运行时校验。

**A（行动）**（5 个组件）：
1. `lib/api/safeCall.ts`：`ApiError` 类（带 code/message/original/tag）+ `parseHttpError()`（FastAPI 422 解析）+ `safeCall()` 包装
2. `lib/api/useNotify.ts`：`useNotify()` hook（toast + inline 两路反馈 + ABORTED 自动吞）
3. `lib/api/useApi.ts`：包装 `safeCall` + 自动 setInlineError + loading + clearError
4. `types/sse.ts`：4 个 SSE 端点全部事件 schema + `safeParseEvent()` + `SSEEventIdSchema`（带 trim）
5. `app/(main)/error.tsx`：路由级 ErrorBoundary（antd `<Result>` + reset() + console.error 上报）

**R（结果）**：26 个新单测（safeCall 12 + useNotify 6 + useApi 8 + sse 11）；6 个 page + 1 个 login 全替换；0 errors 0 warnings；127 tests passed。

### 60 秒口播

> 前端 3 套错误反馈散落——message 警告、Text danger、StreamView 红 panel。我抽 useNotify（toast + inline）和 useApi（自动捕获 + loading）两个 hook，加 ApiError 归一化错误类，zod 给 SSE 事件做运行时校验。26 个新单测，6 个 page 全替换，0 warning。

### 3 分钟展开

**最关键发现**：jsdom `DOMException` `instanceof Error` 不一定为 true——直接用 `name === 'AbortError'` 字符串判断更稳。**Aborted 用户主动取消**必须静默（toast 跳过），不能报错扰民。

### 可追问点

- Q27（3 套错误反馈怎么统一）→ useNotify/useApi 收敛
- Q11（intent 路由 prompt 契约）→ zod schema 锁死

---

## 故事 6：CourseFields 共享字段层抽取（路 7）

### STAR

**S（场景）**：CourseInlineCard（流式推荐输出，~150 行）+ recommend/CourseCard（静态结果，~175 行）共享 **~80% 字段**（teacher/credits/campus/time_slot + 7 tags）——双份维护成本高，路 6 a11y 升级已暴露"行为漂移"风险（一个加了 `aria-label` 一个没加）。

**T（任务）**：抽 `CourseFields` 共用层，2 个 variant（`inline` 流式 / `card` 静态）切样式风格而非字段差异。

**A（行动）**（3 步）：
1. **抽 `components/CourseFields.tsx`**：`variant: 'inline' | 'card'` 参数决定样式；不含独有字段（序号/评分 Tooltip/match_reasons）；不含外层 a11y（role/aria-label 由父组件负责）
2. **改 CourseInlineCard / CourseCard 用 `<CourseFields variant="..." />`**
3. **删 ~120 行共享字段代码，新增 18 个 CourseFields 单测**

**R（结果）**：18 个 CourseFields 单测通过（variant 切换 + 字段完整性 + a11y landmarks + 独有字段隔离）；两个父组件 spec 通过率不变；0 errors 0 warnings；127 → 145（净 +18）。

### 60 秒口播

> 两个课程卡片 80% 字段重复——双份维护高且 a11y 易漂移。抽 CourseFields 共享层，variant 参数切样式不切字段。删 120 行加 18 个新单测。两个父组件行为完全不变，0 warning。

### 3 分钟展开

**关键设计边界**：CourseFields **不**含外层 a11y（role/aria-label 由父组件根据容器决定），**不**含独有字段（序号 / 评分 Tooltip / match_reasons 留在各自父组件）——否则又会变成"另一种大组件"。

### 可追问点

- Q26（为什么抽 CourseFields 共享层）→ 80% 字段重复 + a11y 漂移
- Q25（前端流式输出卡顿）→ rAF + 取消

---

## 故事 7：Phase 3 真实端测兑现（live eval 6/6 + 2/2 + 4/4）

### STAR

**S（场景）**：Phase 3 编码完成（335 单测 + 10 路由 build + 6 个 eval 集 smoke 通过）——但**真实 LLM 端测一直延期**（"未来承诺"挂账）。

**T（任务）**：上游 LLM 配额到位后，**严格跑真实端测**并**只覆盖涉及 Phase 3 改动的功能**（新实装工具 + 记忆改造），不重复跑未改动集（image_generate / web_search 等）。

**A（行动）**（3 步）：
1. **切换 LLM 模型**：`qwen3.5-flash` → `deepseek-v4.1-flash`（算力可用）
2. **Docker rebuild**：镜像源改 `docker.m.daocloud.io/library/python:3.12-slim`（registry-1.docker.io 不可达）
3. **跑 3 集 live**：`evaluation_comment_live`（6/6）/ `report_math_live`（2/2）/ `chat_intent 4 失败 case`（4/4 修复后通过）

**R（结果）**：
- `evaluation_comment_live-2026-08-17.json`：**6/6 通过**（71 门课 / 144.5 学分 / 加权 85.85）
- `report_math_live-2026-08-18.json`：**2/2 通过**（37 学生 PDF 全成，failed_students=0）
- `chat_intent-2026-08-18.json`：**4/4 修复后通过**（intent_04/05/06/07 教师端 dispatch_module 路由）
- 试金石 #1/#5/#10 真实端测兑现 ✓

### 60 秒口播

> Phase 3 编码完成但真实 LLM 端测一直延期。算力到位后切换 deepseek-v4.1-flash + 改 DaoCloud 镜像源，跑 3 集 live 端测。评价 6/6（71 门课加权 85.85）、报告 2/2（37 学生 PDF 全成）、chat_intent 4/4 修复后通过。Phase 3 试金石 #1/#5/#10 全部兑现。

### 3 分钟展开

**为什么"只覆盖涉及 Phase 3 改动的功能"**？image_generate / web_search 等没改动，重跑是浪费配额；只跑涉及"新实装工具 + 记忆改造"的 3 集。同时**也跑了一次 chat_intent 失败 case 修复**——证明路 1 的 prompt 修复在真实 LLM 上生效。

### 可追问点

- Q9（教师端 4 失败 case 修复）→ 路 1 复盘 + live 4/4
- Q19（评价反幻觉实现）→ live 6/6 + 真实 71 门课
- Q13（v1 supervisor 5 agent 流水线）→ smoke 20 case

---

## 故事 8：docker dev proxy 502 修复（路 5）

### STAR

**S（场景）**：v2 阶段所有 live eval 都被 **docker desktop 偶发 502** 卡住——host → container:8000 的转发层 bug。

**T（任务）**：根治 host→container 502，让 frontend 跑起来不被转发层干扰。

**A（行动）**（3 步）：
1. **缓解**：`next.config.ts` 默认 `localhost:8000` → `127.0.0.1:8000`（强制 IPv4 解析，减少一次 DNS 解析）
2. **根治**：新增 `frontend/Dockerfile`（node:20-slim 镜像，dev 模式启动），`docker-compose.yml` 新增 `frontend` 服务（`profiles: ["frontend"]` + `API_PROXY_TARGET=http://python-api:8000`）
3. **验证**：`docker compose --profile frontend up -d` 启动 → 容器内 frontend 走 service 名直连 python-api → 完全绕开 host proxy

**R（结果）**：dev 阶段 `127.0.0.1:8000` 缓解大部分 502；`docker compose --profile frontend up -d` 启动后**无 502**（容器内直连）；前端 Dockerfile 走 Turbopack dev 模式（与 host 上 `npm run dev` 行为一致）。

### 60 秒口播

> docker desktop 转发 host→container 偶发 502 卡所有 live eval。我先 127.0.0.1 缓解，然后新增 frontend Dockerfile + docker-compose profiles，容器内走 service 名直连 python-api，彻底绕开转发层。docker compose --profile frontend up 启动后无 502。

### 3 分钟展开

**根治 vs 缓解**：缓解在 host 配置层（改 127.0.0.1），根治在容器化层（frontend 容器也进 docker 网络）。**关键设计**：`profiles: ["frontend"]` 默认不启 frontend（避免 LLM 慢测试时阻塞 host 开发流），仅 `docker compose --profile frontend up` 时启动。

### 可追问点

- Q28（docker 转发为什么 502）→ docker desktop bug + 127.0.0.1 缓解 + 容器化根治
- Q1（为什么用 deepagents）→ Phase 0 POC 验过中转站兼容

---

## 故事选择建议

| 面试方向 | 优先选 | 备选 |
| --- | --- | --- |
| AI Agent | 故事 1（main_agent 路由）+ 故事 4（dispatch_module） | 故事 5（zod 错误层） |
| 后端工程 | 故事 3（SSE 续传）+ 故事 7（live eval 兑现） | 故事 8（docker 502 修复） |
| 推荐系统 | 故事 2（评价反幻觉）+ 故事 1（main_agent） | 故事 7（live eval） |
| RAG | 故事 2（评价反幻觉）+ 故事 1（main_agent） | 故事 3（SSE 续传） |

## 讲故事避坑

- ❌ 不要讲 v1 supervisor 双模式作为"v2 核心"——它是 v2 main_agent 的**子 agent**
- ❌ 不要讲"我设计了 5 个智能体"——v2 是 4 个业务模块 + 5 个 MCP 工具，supervisor 内部才有 5 agent
- ❌ 不要讲"实现高并发推荐"——没压测数据
- ❌ 不要讲"全量生产可用"——A/B react 组还没在生产自动分流
- ❌ 不要把 v1 → v2 改得太大（"全部重写"）——v2 是 v1 supervisor + dispatch_module + RAG + SSE 续传的**渐进升级**
