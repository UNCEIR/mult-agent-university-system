# v2.0.0 实施总计划

> 本文件是 v2.0.0 升级的**总计划**，按 Phase 概要组织。具体设计决策与论证细节见 `notes/`，每个 Phase 的**详细实施计划**将单独生成（见"后续步骤"），再据详细计划分阶段编码。
>
> 维护：本文件只记 Phase 级概要与索引，决策细节变更请同步 `notes/` 与对应 Phase 详细 plan。

## 当前执行边界（2026-08-08）

- 当前优先交付统一的 deepagent 工厂：所有业务 Agent 都通过 `python/agent/main/factory.py` 创建，再由场景规格注入各自的 system prompt、skills、memory、工具 allowlist 和 checkpointer。
- 当前阶段完全不接入 FastGPT、FastGPT MCP、FastGPT client 或 FastGPT Compose 服务；相关旧计划内容保留为后续阶段背景，不作为当前编码任务或验收条件。
- 当前阶段不为通过旧测试而回退业务设计；测试应验证统一工厂和业务场景契约，不能要求 report/evaluation/PPT Agent 继续保持 `NotImplementedError` 占位行为。
- 已落地知识库 RAG（学生手册 public 分区 + 个人成绩单 user 分区）：`query_knowledge` 工具、`document_vector_repo`/`document_repo`、脱敏器、recursive 分块、`scripts/ingest_student_handbook.py`、`scripts/ingest_transcript_desensitized.py`。
- 推荐接口已收敛：v1 遗留端点（同步 `/api/v1/recommend`、`/recommend/react`、`/recommend/react/stream`、`/recommend/graph`）删除，统一为 `POST /api/v1/recommend/stream`（默认并行 Pipeline 最快，`mode=react` 走 ReAct 可选）。`recommend_courses` 工具内部走同一 `supervisor.stream_recommend_unified`，与前端入口同源。

## RAG 核心评估指标（LangSmith，待端测定基线）

> 这些指标依赖真实端到端测试收集数据后才能定阈值与优化方向；当前仅记录为验收与监控目标，指标埋点后续 Phase 落地。
>
> **评估方针（2026-08-12 决策）**：Phase 2 落地首批 eval set 骨架（`python/eval_sets/`：chat_intent / report_math / evaluation_comment / kb_retrieval + `eval/runner.py` 断言式指标 + LangSmith Dataset 导入脚本，见 `plans/phase-2-report-evaluation.md` Step 10）；**全量指标（LLM-as-judge、NDCG、monitor 看板、意图识别准确率/工具调用成功率/幻觉率/延迟全套）在 Phase 4 落地**，直接消费 Phase 2 的 trace 埋点与 eval_sets。

| 指标 | 定义 | 目标 | 采集方式 |
|---|---|---|---|
| **上下文召回率 (Context Recall)** | 检索返回的 top-k 片段中，包含回答所需关键事实的比例 | 待端测定基线（初始目标 ≥0.7） | LangSmith trace 记录 query → 检索 hits → 回答；标注集标注"应命中的 chunk" |
| **忠诚度 (Faithfulness/Groundedness)** | 回答中的陈述是否都能由检索到的片段支持（无幻觉/无源编造） | 待端测定基线（初始目标 ≥0.8） | 逐句核对回答陈述是否在检索片段中有依据；LangSmith rubric 或人工标注 |
| 辅助指标 | 检索延迟 P50/P95、缓存命中率、LLM 生成延迟 | 记录分布，不设硬阈值 | `/metrics` + LangSmith |

**优化策略（待端测后按数据驱动）**：top_k 调整、chunk 大小/overlap、混合检索（BM25+向量）、索引参数（HNSW）、rerank、query 改写、prompt 输出约束。

## 增量更新与旧知识干扰规避

- **数据集粒度去旧**：`delete_by_dataset(dataset_id)` + `replace_chunks` 以 dataset 为单位整体替换，重跑摄入即清理旧版本，避免新旧文档向量混杂。
- **分区隔离**：手册 public / 成绩单 user 分区互不干扰；个人数据不进入共享检索。
- **版本号**：dataset_id 带内容 hash（如 `handbook_2025_<hash>`），内容变化 → 新 hash → 新 dataset，旧版按需删除。
- **增量建议**：定期重跑 `ingest_student_handbook.py`（幂等去旧）；新增文档走 `documents/upload` 同 pipeline。

## 背景

- **v1.0.0 现状**：公选课推荐系统（大学校园多智能体平台的前身）已工作（固定 Pipeline + ReAct 双模式、SupervisorOrchestrator、MySQL+Milvus+Redis、7 个 ReAct 工具、A/B 实验）。本质是"一个推荐接口 + 简陋 RAG"，多 agent 活性不足、项目深度不够。
- **v2.0.0 愿景**（见 `需求.md`）：新业务广度 + 知识库基础设施 + Agent 工程深度 + Skills 系统 + 跨语言 + 框架选型。
- **目标**：先建平台基座，用"成绩单报告 + 评价寄语"两个学生场景验证。

## 设计决策索引（细节见 notes）

15 个决策 + 智能体重构已定稿，详见：
- `notes/2026-07-27-设计决策问答记录.md` —— 决策问答记录（问题/选项/用户选择）
- `notes/2026-07-28-设计决策补充说明.md` —— 决策补充论证（决策 2/6/8 + 决策 5 修订 + 智能体重构等）

**决策速览**：
| # | 决策 | 选择 |
|---|------|------|
| 1 | 首要交付物 | 平台基座 + 成绩单报告验证 |
| 2 | 编排基座 | deepagents（A+B 统一，建在 LangGraph 之上） |
| 3 | 框架 | deepagents |
| 4 | v1 共存 | 包装为 subgraph，暴露为 tool |
| 5 | 成绩单报告 | 批量 Excel→1.html 模板→WeasyPrint PDF（每学生独有链接） |
| 6 | MinIO/文档流水线 | 双角色 + API 化摄入 + 通用知识 Q&A；KB 走 FastGPT 二次开发 |
| 7 | Skills 系统 | 原生 tools + Jinja2 HTML→WeasyPrint PDF |
| 8 | 跨语言 TS | MCP 桥接（v2 先接 FastGPT mcp_server） |
| 9 | 报告获取推荐 | 共享 tool `recommend_courses` |
| 10 | 路由 | 混合入口，统一 main agent 对话框 + 独立模块页 |
| 11+12 | 可靠性 | deepagents 内置 + v1 + 源码模式 |
| 13 | API 端点 | `/chat` 统一入口 + `/report` `/evaluation` `/documents/upload` 保留 `/recommend` |
| 14 | 课程富化 | MySQL 结构化（1.html 仅结构参考，实际大学公选课） |
| 15 | 迁移 | POC 先行 + 4 阶段 |

**智能体重构**：成绩统计智能体（`/report`，报告卡 + 成绩记载功能 + 流式评价叙述）+ 评价寄语 agent（`/evaluation`，comment_type 四种驱动）。两个独立智能体，对话不共享。

## 新增决策（2026-08-07 定稿）

### 决策 16：前端页面架构 —— 统一对话框 + 独立模块页
- **MainPage**（统一对话框入口）：main agent 提供 chat（知识库问答 + 网页搜索 + 深度思考 + 论文写作）+ recommend（路由到推荐 skill）+ report（路由到报告模块）+ evaluation（路由到评价模块）
- **ImageGeneratePage**（独立页）：图片生成多模态插件，独立于 main agent 的专门页面
- **PPTGeneratePage**（独立页）：PPT 生成系统，独立于 main agent 的专门页面
- **理由**：图片生成和 PPT 生成是深度交互场景（需要画布/预览/参数配置），不适合嵌入对话流；而 recommend/report/evaluation 是对话式查询场景，适合在对话中展示结果

### 决策 17：Main Agent 能力边界
- **chat agent 核心能力**：知识库问答（学生手册.pdf 知识库）+ 网页搜索（tavily）+ 深度思考（独立思考模式）+ 论文写作（writing_assistant tool）
- **独立模块**：课程推荐、报告生成、评价寄语、PPT 生成 → chat agent 仅负责意图识别 + 路由
- **图片生成**：从 chat agent 解耦，独立 Page 组件，经 MCP 调图片生成服务
- **理由**：职责分离 —— 对话式查询在 chat 中完成，深度交互在独立页面中完成

### 决策 18：论文写作功能
- main agent 新增 `writing_assistant` tool，LLM 驱动，支持多体裁/多风格论文写作
- 在 chat 对话框中自然交互：用户提出写作需求 → main agent 识别意图 → 调用 writing_assistant tool → 对话中生成论文
- **理由**：论文写作是对话式创作过程，适合在 chat 中完成，不需要独立页面

## 新增决策（2026-08-14 定稿：记忆分级 / checkpoint 存储 / 跨语言通信）

### 决策 19：长期记忆分级 —— AGENTS.md 只承载"全局共享记忆"，用户数据一律走表
- **可承载**（全局共享、非租户）：平台静态背景、技能索引（现有）、**全局运维记忆**（外部凭据未配状态、已知模型行为漂移、批处理推荐时段）——由代码/人工维护，**LLM 写回仍被 FilesystemPermission 代码级禁止**
- **禁入**：任何用户级数据（偏好/事实/决定）→ 一律 `chat_memory_entries` 表（user_id 分区隔离）
- **边界判定**：一条数据问"另一个用户能看到它吗？"——能且无碍 → 候选 AGENTS.md；涉及任何个体 → 必须表（企业级多租户记忆切分：全局配置 vs 用户画像）
- **排除项**：全局公共知识（校园通知/校历）走知识库 public 分区，不进 AGENTS.md（AGENTS.md 是"给 agent 的指令记忆"，不是"给用户的检索内容"）；群体运营指标走 /metrics
- **理由**：多租户隐私隔离是硬约束；文件承载用户数据会引入共享文件竞态/无校验/无审计/无法增量提取（见 Phase 2 决策 26）

### 决策 20：checkpoint 存储 —— RedisSaver 切换条件为"多实例部署"，单实例维持 SqliteSaver
- **现状**：AsyncSqliteSaver（本地 `.checkpoint.db`），单实例下语义最强（磁盘持久）、运维最简
- **切换条件（显式，缺一不切）**：python-api 实例数 > 1（滚动更新/水平扩容，需多副本共享 thread_id 会话恢复）时才迁移 `langgraph-checkpoint-redis`
- **分层原则**：checkpoint 是"**可重建的运行态**"而非"不可丢失的事实"——事实在 MySQL（chat_messages / report_artifacts / evaluation_records）；Redis 重启丢失会话恢复可接受，历史不丢（MySQL 兜底）
- **否决理由（单实例下）**：RedisSaver 多一跳网络 + 持久化语义更弱（依赖 AOF/RDB 配置），是负优化
- **执行**：Phase 3 按此条件评估，条件不满足则推迟；requirements 在切换时再加 `langgraph-checkpoint-redis`

### 决策 21：跨语言通信 —— REST 同步 / RabbitMQ 异步 / SSE 流式 / MCP 工具化
- **决策树先行**（先问通信形态再选技术，不先选技术再套场景）：
  1. 同步请求-响应（工具调用/CRUD 查询）→ **REST/OpenAPI**
  2. 异步任务/解耦（批量生成/通知/重试）→ **RabbitMQ**（Phase 3+ 引入）
  3. 流式/长连接 → **SSE**（现状已达标）
  4. 跨语言工具调用（Java/TS 能力暴露给 agent）→ **MCP**（决策 8，Python 侧 MultiServerMCPClient 已实装）
- **场景与选型**：
  | 场景 | 量级 | 选型 | 否决理由 |
  |------|------|------|---------|
  | Python agent ↔ Java 数据服务（查询） | 低频 <100 QPS | REST/OpenAPI | gRPC 收益在"高频内部网格+双向流"，代价=proto 契约管理/调试链割裂/与 SSE 生态脱节；跨语言面越窄越优先最简可互操作协议 |
  | 批量任务（report/evaluation/记忆提取/PPT） | 日数百级 | RabbitMQ | Kafka=百万 msg/s 事件流，吞吐浪费+运维重；RabbitMQ 队列/ack/死信/延迟队列语义与任务量级匹配 |
  | 实时流式 | 对话级 | SSE | 单向推送+自动重连+HTTP 兼容；WebSocket 留给双向交互场景（后续评估） |
  | 跨语言工具化 | 工具级 | MCP | 统一"工具即协议"，Python 侧熔断/降级链直接复用 |
- **修订（2026-08-15）**：image_generate（即梦）接入形态从"外部即梦 MCP server"修订为 **自建 stdio MCP server 包装火山引擎 API**（火山无官方 MCP）：`tools/image/jimeng_mcp_server.py`（generate_image_submit / generate_image_get 两段式）+ `jimeng_client.py`（SDK 签名/提交/轮询/错误码分类）。两段式链式调用（B1）：agent 提交拿 task_id → 按指数退避（3→6→10s 封顶）轮询 get → done 转存 MinIO/本地；force_single 默认 false（组图 1-3 张由 prompt 语义控制，scale 默认 0.7 增强文本遵从）；错误码分类（审核不可重试/限流可重试）。**A（工具内阻塞轮询）vs B（两段式链式）取舍**：B 的每环状态可观测、逐环独立兜底、task_id 12h 有效可续查——见 `docs/notes/v2.0.0/2026-08-15-jimeng-mcp-chain.md`
- **执行**：Java 数据服务（Phase 3/4）暴露 OpenAPI；批量任务队列在任务量出现并发时引入 RabbitMQ；MCP 桥接按决策 8 推进

### 决策 22（2026-08-16）：前端 BFF 代理层预留 —— 为什么在 Next.js 留 `app/api/`
- **背景**：后续部分数据 CRUD 转 Java 数据服务（REST/OpenAPI，决策 21），前端将面对 **Python（SSE 流式）+ Java（REST CRUD）双后端**；未来还可能有独立管理平台（同 React 栈）。
- **BFF 作用**：前端**永远只请求自己的 `/api`**，由 Next.js Route Handlers 在服务端转发到 Python 或 Java——前端对"后端是谁"无感，Java 接入/地址变动/鉴权聚合都不改前端。
- **结论**：`app/api/` 仅**预留目录 + 文档**（本阶段零代理逻辑），当前前端仍经 rewrites 直连 Python 8000；分线策略：Python SSE 直连、Java REST 走 BFF。

## Phase 概要

### Phase 0：deepagents POC（go/no-go 门）—— ✅ GO（2026-07-29）
- **目标**：验证 deepagents + 中转站（`one.zhique.cn` ChatOpenAI）+ Docker 兼容
- **交付**：POC 脚本（最小 main agent + 1 tool，经中转站调用）在 Docker 内跑通
- **门控**：失败 → 回退决策 2 备选（LangGraph 混合 / OpenAI Agents SDK）
- **结果**：三轴全绿（deepagents 0.6.12 可用 / 中转站 tool-calling 双向兼容 / Docker 构建运行通过）；v1 回归 44 通过 3 预存失败（A/B 路由问题，非依赖升级回归，见详细计划 §4.1）。**GO，进入 Phase 1**
- **详细 plan**：`notes/2026-07-29-phase-0-deepagents-poc详细计划.md`（已生成并执行）

### Phase 1：平台基座
- **目标**：搭建 deepagent 主 agent + tool 注册框架 + v1 包装 + MinIO + 文档流水线 + Skills 注册 + 记忆机制
- **交付**：
  - deepagents 主 agent + tool/subagent 注册表（Pydantic，MCP-ready），tools详情放在`python/tools/`。
  - v1 推荐链路包装为 `recommend_courses` tool（LangGraph subgraph）
  - MinIO 双角色（源文档 + 报告 artifact）
  - 文档流水线：走 FastGPT KB 二次开发（HTTP+MCP 调用）+ Python CSV/PDF/doc 解析兜底
  - Skills 技能文档目录（SKILL.md，deepagents SkillsMiddleware 自动加载），`python/skills/`已初始化完毕
  - **记忆管理提前实装 demo**：`MemoryMiddleware`（长期记忆，FilesystemBackend 真实 `AGENTS.md`）+ `SummarizationMiddleware`（compaction，阈值对齐决策 11 `contextWindow-13000`/`keepRecentTokens=20000`，落盘 `/conversation_history/{thread_id}.md`）+ `FilesystemMiddleware`（大 tool result 落盘）+ `SkillsMiddleware`（渐进式 skill 披露）+ `SqliteSaver`（thread_id 跨会话恢复，本地 sqlite），实装 `POST /api/v1/chat` 端点
  - 前端三个 Page 组件：`MainPage`（chat 统一入口）、`ImageGeneratePage`（图片生成）、`PPTGeneratePage`（PPT 生成）
  - 论文写作功能：`writing_assistant` tool 在 main agent 中
- **验证**：`/recommend` 仍工作；文档上传→MinIO+MySQL/Milvus 入库；`/chat` 多轮对话 + 记忆 + compaction 正常
- **详细 plan**：`plans/phase-1-platform-base.md`（已生成，2026-08-05 grill-me 沉淀 33 个子决策）+ `plans/phase-1-coding-plan.md`（记忆机制实装计划，2026-08-07）

### Phase 2：报告 + 评价寄语场景（MVP 主交付）—— ✅ 编码完成（2026-08-13，待 docker 验收）

- **目标**：两个学生场景智能体跑通
- **交付**：
  - **成绩统计智能体**（`/report`）：批量 Excel→单科 JSON→学生 JSON→Python 加权复合统计 + 填 1.html Jinja2 模板→WeasyPrint PDF（每学生独有下载链接）+ 成绩记载功能（score JSON→comment）+ 流式评价叙述
  - **评价寄语 agent**（`/evaluation`）：输入 studentList JSON（comment_type 四种 + teacherSubjectiveEvaluation + scoreList）→ LLM 按 comment_type 生成 comment
  - **前端**：MainPage 中嵌入 report/evaluation 对话入口，结果在对话框中展示
- **验证**：`/report` 返回每学生 PDF 链接、加权正确；`/evaluation` 返回 comment、数值引用正确
- **详细 plan**：`plans/phase-2-report-evaluation.md`（已生成，2026-08-12，含 report/evaluation 场景 + 主 agent 插件 MCP + 记忆机制 + 评估方针骨架 五交付面）+ `plans/phase2-coding-plan.md`（编码执行清单，2026-08-12，编码完成 2026-08-13）
- **编码结果**：W-A~W-J 全部落地（report A-shell 四决策点/反幻觉五层/MCP 三服务器/chat 记忆/评估骨架），`pytest -m "not slow"` = 235 passed；docker 重建验收 + 端到端冒烟待执行

### Phase 3：扩展收尾 + 前端统一开发 + PPT 场景 —— ✅ 本阶段范围编码完成（2026-08-16）
- **目标**（2026-08-16 范围修订，见 `plans/phase-3-extensions.md`）：A 既有缺口修复 + B 前端架构迁移（Vite→Next.js）与四 Page + C 记忆管理深化；PPT/FastGPT/Java 数据服务后置后续 phase
- **交付（已完成）**：
  - A：`image_generate_get` 注册、`compute_weighted_grade` 实装、`RecommendationRequest.mode` 透传、evaluation 三函数 @tool 化、注册一致性测试锁死、eval oracle 采集脚本 + `evaluation_comment_live`/`report_math_live` 集、`_live_report_math` 端到端化、skill 引用修正
  - B：前端迁移 **Next.js 16（App Router，React+TSX）**，六菜单导航（推荐/智能对话/报告/评价/知识库/监控），MainPage(chat SSE 流)/ReportPage/EvaluationPage(echarts 雷达)/DocumentsPage 四 Page，SSE 客户端层（lib/sse + lib/api），失效端点清零，`app/api/` BFF 预留（决策 22）
  - C：summary_prompt 对齐决策 11 五字段、记忆提取 forked worker（MemoryExtractWorker）、consolidation（去重+LLM 合并）、checkpoint_backend 配置预留（决策 20，单实例维持 SqliteSaver）
- **验证**：`pytest tests/ -m "not slow"` = **267 passed**；`frontend npm run build` 通过（六路由）；eval smoke 全量绿；真实 LLM 端测（live eval/真实对话/report PDF/evaluation 生成）因算力受限延后，算力允许时补跑
- **详细 plan**：`plans/phase-3-extensions.md`（已生成）+ `plans/phase3-coding-plan.md`（W-A~W-F 完成）
- **后续 phase 待办**（写入 §7 输入清单）：PPT 生成系统（`ppt_generate` DSL→PPTX + PPTGeneratePage + `skills/ppt-generation` 填充）、图片生成独立 Page、FastGPT 二次开发桥接（重新评估）、Java 数据服务（身份/鉴权/REST+MQ）、独立管理平台（React 栈）、RedisSaver 实际迁移（决策 20 条件）、LLM-as-judge 全量指标（Phase 4）

#### Phase 3 真实端测兑现（2026-08-18，按 phase-3-extensions.md §1.4 试金石 ⏳ 项）

> 兑现 phase-3-extensions.md 第 58 行"未来承诺"：上游资源到位后严格跑真实端测。范围只覆盖涉及 Phase 3 改动（新实装工具 / 记忆改造）的 eval 集，未改动的 `image_generate` / `web_search` 等不纳入。
>
> **入口切换**：原 `qwen3.5-flash` 算力余额不足，已切换至 `deepseek-v4.1-flash`；docker 镜像 `--build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim` 重建（registry-1.docker.io 不可达，改用 DaoCloud 镜像源，缓存复用 5/6 层）。
>
> **结果**：
>
> | eval 集 | 范围 / 与 Phase 3 关系 | 结果 | 报告 |
> |---------|----------------------|------|------|
> | `chat_intent`（仅 5 失败 case：`intent_04/05/06/07/20`） | 触发 main agent 工具链 + summarization 五字段 prompt 改造 | 1/5（仅 `intent_20` 通过；其余为既有意图路由语义，**与 Phase 3 改动无直接关联**） | `eval/reports/chat_intent-2026-08-17.json` |
> | `evaluation_comment_live` | **直接调用** Phase 3 实装的 `design_dimensions` / `compute_radar_values` / `generate_comment` 三 @tool（含 `compute_weighted_grade` 公式 0.3+0.7+bonus） | **6/6 通过**（学生 3123003252 真实成绩单；4 类 comment_type 全绿；不存在用户正确返回 `no_transcript_data`） | `eval/reports/evaluation_comment_live-2026-08-17.json` |
> | `report_math_live` | 端到端消费 `/api/v1/report` SSE；触发 `compute_weighted_grade` 与 `RecommendationRequest.mode` 透传链 | **2/2 通过**（37 学生 PDF 全部生成，无 failed_students） | `eval/reports/report_math_live-2026-08-18.json` |
>
> **结论**：Phase 3 试金石 #1（注册一致性 + compute_weighted_grade 实装）、#5（eval oracle 对齐 live 端测）、#10（验收回归 live 部分）三项 ⏳ 项已通过真实端测兑现。`chat_intent` 4 个 case 失败属于 chat 路由层既有行为，超出本 phase 编码范围，留待 Phase 4 chat_intent NLU 调优。

### Phase 4：深度增强（体现工程深度）
- **目标**：端到端评测 + monitor 在线表现 + 检索指标驱动调优 + 多模态 + agent harness 深化 + 兜底演示
- **交付**：
  - **端到端 agent 评测**：意图识别准确率、工具调用成功率、检索召回率/精度/F1/NDCG、幻觉率、端到端延迟；评测测试集（**承接 Phase 2 eval_sets 首批 4 集扩为全量**）+ 指标看板（复用 v1 `/metrics` + `prometheus-client` + LangSmith Dataset/evaluator：代码断言 / LLM-as-judge / 对比）
  - **monitor agent 在线表现**：`/metrics` 监控退化/异常 → 指标驱动检索策略调优（top_k/语义缓存阈值/分块策略/rerank 权重）
  - **多模态 LLM 接入**：通用 agent 加入图谱识别/图片识别（如课程图谱可视化、成绩趋势图识别）
  - **插件市场**：用户在 FastGPT 侧自建 agent/KB/插件，Python 主 agent 经 MCP 动态发现调用
  - **agent harness 深化**：think→act→observe 循环可视化、工具调用链路追踪（OpenTelemetry）、subagent 委派树可视化、checkpointing 恢复演示
  - **工具链路断裂兜底演示**：故意断工具（FastGPT KB 不可用）→ circuit breaker 熔断 → Python 兜底脚本 → 部分结果保留 → checkpointing 恢复 → 降级运行
  - **幻觉兜底演示**：LLM 试图自算统计 → schema 约束拦截 → 引用文件数值 → compaction 摘要落盘 → subagent 隔离
- **验证**：评测指标可观测；多模态可用；插件动态发现；harness 可视化；断裂/幻觉兜底演示通过
- **详细 plan**：`plans/phase-4-depth-enhancement.md`（待生成）

## 贯穿原则（跨 Phase 持续遵守）

### 原则 1：agent 编排 vs 能力分离
- `agent/` 只做编排（路由/委派/调度/对话管理），不持有具体能力实现。
- `tools/` 放原子能力（PDF 解析、分块、向量化、渲染、搜索、插件），以 `@tool` 装饰器 + Pydantic `args_schema` 暴露。**ToolRegistry/CircuitBreaker/MCPClient 也放 `tools/`**（工具注册发现层）。
- `skills/` 放 **SKILL.md 技能文档**（deepagents SkillsMiddleware 自动加载的渐进式披露指令），每个技能一个子目录 + `SKILL.md`，不是 Python 代码层。
- 原则：每个 tool 独立 `.md` 说明文档，每个 skill 独立 `SKILL.md` 指令文档，CLAUDE.md 只记索引。

### 原则 2：deepagents 优先，FastGPT 拖拽并存
- Phase 1-2 先用 deepagents 框架编码实现（`create_deep_agent` + `@tool` + ToolNode）。
- Phase 3 起引入 FastGPT 拖拽构建，作为同业务的备选/增强实现。
- 两者不冲突，通过 feature flag 切换（`config/settings.py` 中 `knowledge_provider` 等开关）。
- 拖拽产物（FastGPT app/workflow）对应 Python tool 的配置映射，在 `tools/` 中维护映射表。

### 原则 3：通用 chat 插件体系（2026-08-07 修订）
- main agent（MainPage 对话框）内嵌能力：
  - **知识库问答**：学生手册.pdf 知识库查询
  - **网页搜索**：tavily 实时搜索
  - **深度思考**：独立思考模式分析复杂问题
  - **论文写作**：`writing_assistant` tool，支持多体裁/多风格
  - **课程推荐**：路由到 `recommend-courses` skill
  - **报告生成**：路由到 report 模块
  - **评价寄语**：路由到 evaluation 模块
- 独立 Page 组件（非对话框内）：
  - **图片生成**：`ImageGeneratePage`，经 MCP 调图片生成服务
  - **PPT 生成**：`PPTGeneratePage`，DSL→PPTX 渲染管线
- 插件走 ToolRegistry 注册 + allowlist 门控，支持。。 MCP 动态发现接入外部插件。

### 原则 4：前端页面架构（2026-08-07 新增）
- 三个 Page 组件：
  - `MainPage`：统一对话框入口，承载 chat（知识库问答+网页搜索+深度思考+论文写作）+ recommend + report + evaluation
  - `ImageGeneratePage`：图片生成独立页面（深度交互场景，需画布/参数配置）
  - `PPTGeneratePage`：PPT 生成独立页面（深度交互场景，需画布/动画/模板选择）
- 导航栏：左侧菜单栏或顶部 Tab 切换三个页面
- v1 兼容：保留 `/recommend` 独立推荐页作为快速入口

## 待决开放项

1. **Excel→JSON 解析方式**：openpyxl（格式固定）vs LLM 提炼（格式多变）——需看实际 Excel 样本
2. **FastGPT KB 存储拓扑**：自带 Mongo vs 复用 MySQL/Milvus/MinIO——Phase 1 集成时研究配置再决
3. **种子文档集**：大小/格式/来源（内容与大学挂钩）

## 后续步骤

1. ✅ 总 plan 完成（本文件）
2. ✅ 决策笔记同步（07-27/07-28）
3. ✅ CLAUDE.md 深度要求 + plan 模式自动参考规则
4. ⏭ **按每个 Phase 单独生成详细 plan.md**（`plans/phase-N-*.md`，含具体文件、函数、步骤）
5. ⏭ 据详细 plan **分阶段编码**，每阶段验证闭环跑通后再进下一阶段
6. Phase 0 POC 优先（go/no-go 门）——deepagents 兼容性是最大未验证风险
