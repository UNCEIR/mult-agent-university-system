# CLAUDE.md

本文件为 Claude Code（claude.ai/code）在此仓库中工作时提供指导。

每次提问后大模型的回答 -- 必须带有问候“主人”二字

window系统调用bash工具时--调用 shell 优先级的顺序：window下linux子系统的wsl > powershell > cmd（Windows）> git bash。


## 项目简介

大学校园多智能体平台 —— 面向大学生校园场景的多智能体系统：公选课推荐、知识库问答（学生手册/个人成绩单）、成绩报告、评价寄语等。

当前分支：`feature-v2.0.0-phase1` | 主分支：`main`

## 常用命令

```bash
# 虚拟环境
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
python -m pip install -r python/requirements.txt

# Docker 服务
docker compose up -d                                     # 一键启动
docker compose up -d --build python-api                  # 代码修改后重建

# 导入课程数据
cd python && python scripts/ingest_course_dataset.py       # 全量约 500 门
python scripts/ingest_course_dataset.py --limit 20        # 先少量验证
python scripts/backfill_milvus_vectors.py                 # 回填缺失向量

# 测试
cd python
python -m pytest tests/ -v                               # 全部
python -m pytest tests/ -m "not slow"                    # 跳过外部服务依赖
python -m pytest tests/ --cov --cov-report=term-missing  # 带覆盖率
python -m pytest tests/test_main_agent_memory.py -v      # 单个文件

# API 冒烟
curl -sS -X POST http://localhost:8000/api/v1/chat -H "Content-Type: application/json" \
  -d '{"message":"你好","session_id":"s1","user_id":"u1"}'
```

## 架构

### 三层分离（v2.0.0 核心原则）

| 层 | 目录 | 职责 | 内容 |
|---|------|------|------|
| 编排层 | `agent/` | 路由/委派/调度/对话管理 | Python 编排逻辑（不持具体能力实现） |
| 能力层 | `tools/` | 原子能力 + 注册发现 | `@tool` + Pydantic `args_schema`，8 个功能域子包 |
| 技能层 | `skills/` | 技能说明文档 | SKILL.md（deepagents SkillsMiddleware 自动加载，非 Python 代码） |

**tools/ 子包**：`system/`（系统级）、`chat/`（对话）、`documents/`（文档）、`recommend/`（推荐）、`image/`（图片）、`code/`（代码）、`mindmap/`（脑图）、`report/`（报告）。
**skills/ 技能**：9 个 SKILL.md（`recommend-courses`、`document-ingestion`、`report-generation`、`evaluation-writing`、`knowledge-query`、`web-search`、`deep-thinking`、`writing`、`ppt-generation`）。

### 推荐链路（v1 核心）

```
POST /api/v1/recommend
  → SupervisorOrchestrator
    → Phase 1: StudentProfileAgent ∥ CourseRecallAgent（宽召回，profile=None）
                └─ 画像成功后 → CourseRecallAgent（精召回，带画像）
    → Phase 1.5: HardConstraintFilter（确定性过滤 —— 校区、类别、考试等）
    → Phase 1.75: LLM 语义初筛（仅在候选 > 40 且画像存在时触发）
    → Phase 2: CourseRerankAgent ∥ CourseFeasibilityAgent
    → Phase 3: RecommendationReasonAgent（串行，依赖最终课程和风险）
```

双模式编排：固定 Pipeline（默认 50%）/ ReAct 工具调用（50%），通过 `ab_test.assign(user_id, "react_vs_pipeline")` 一致性哈希分桶。

### API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/v1/chat` | ✅ 主 agent 统一会话（多轮对话 + 记忆管理 + 意图识别） |
| `POST` | `/api/v1/chat/stream` | ✅ SSE 流式主 agent 会话（text/tool/done/error 事件） |
| `POST` | `/api/v1/recommend/stream` | ✅ 统一流式推荐入口（默认并行 Pipeline 最快；mode=react 走 ReAct 可选） |
| `POST` | `/api/v1/documents/upload` | ✅ 文档摄入（知识库，本地解析/分块/向量化入库） |
| `GET` | `/health`、`/api/v1/health` | ✅ 健康检查 |
| `GET` | `/api/v1/experiments` | ✅ A/B 实验状态 |
| `GET` | `/api/v1/metrics` | ✅ Agent / 业务指标 |
| `POST /api/v1/evaluation` | ⏳ 未注册（Phase 2） |
| `POST /api/v1/report` | ⏳ 未注册（Phase 2） |
| `POST /api/v1/ppt` | ⏳ 未注册（Phase 3） |

> v1 遗留端点（同步 `/api/v1/recommend`、`/react`、`/react/stream`、`/graph`）已删除，统一收敛到 `/api/v1/recommend/stream`。

### 涉及LLM输出的端点：

- 一律采取流式stream的方式输出token回答。

### 关键设计决策

1. **评分职责分离**：`CourseRecallAgent._score_candidates()` 做广度（仅关键词匹配 + 热度），`CourseRerankAgent._compute_score()` 做精度（profile 偏好 + Milvus COSINE 融合 `final = profile_score * (1.0 + milvus_sim * 0.5)`）。规则分为 0 时语义相似度无法"拯救"该课程。
2. **硬约束是确定性过滤**：`HardConstraintFilter` 在重排前移除违规课程，候选不足时返回 `hard_constraint_sparse` 警告，不悄悄放宽。
3. **每个请求只调用一次 Embedding**：`CourseRecallAgent._execute()` 入口处计算一次 `query_embedding`，传递给语义缓存探测、Milvus 搜索和缓存索引三个消费者。
4. **Redis 仅缓存候选课程 ID**（非完整对象），缓存命中后仍回 MySQL 取完整数据。
5. **所有 Agent 继承 `BaseAgent`**：模板方法模式，内置 tenacity 重试、耗时追踪、`_fallback()` 降级。

### Deepagents 主 Agent 记忆机制（v2.0.0 新增）

`POST /api/v1/chat` 使用 `build_main_agent()` 工厂构建的 deepagents agent：

- **短期记忆**：`SummarizationMiddleware(trigger=("tokens", cw-13000), keep=("tokens", 20000))`，demo 可用 `messages=8` 触发
- **长期记忆**：`MemoryMiddleware` + `FilesystemBackend` 真实 `memories/AGENTS.md` 文件
- **Checkpointer**：`SqliteSaver`（本地 sqlite，`thread_id=session_id` 恢复）
- **渐进式 skill**：`SkillsMiddleware` 自动扫描 `skills/` → 注入 skill 索引到 system prompt → LLM 匹配后 `read_file` 读 SKILL.md
- **Backend**：`CompositeBackend(default=StateBackend(), routes={"/skills/": FilesystemBackend, "/memories/": FilesystemBackend})`

### 数据架构

| 存储 | 内容 | 用途 |
|------|------|------|
| MySQL `course_records` | 500 门课程结构化字段 + `raw_json` | 事实来源（v1） |
| MySQL `course_chunks` | 500×4=2000 条文本块 | 块元数据（v1） |
| Milvus `course_chunks_real` | 每门课 4 条向量，维度 1024 | 语义召回（v1） |
| MySQL `document_records` / `document_chunks` | 文档元数据 | 通用文档（v2 新增） |
| Milvus `document_chunks` | 通用文档向量，10 字段 schema | 通用文档语义检索（v2 新增） |
| MinIO `source-documents` / `report-artifacts` 桶 | 文档原件 + 报告产物 | 源文档存储（v2 新增） |
| Redis | 候选课程 ID 列表（TTL 15 分钟） | 召回缓存 |

### LLM 与 Embedding

已废弃： `one.zhique.cn` /v1（OpenAI 兼容协议），共用 `api_key` / `base_url`：
- **LLM**：`ChatOpenAI`（`python/ai/llm_client.py`），模型 `deepseek-v4.1-flash`
- **Embedding**：`OpenAIEmbeddingClient`（`python/ai/embedding_client.py`），模型 `text-embedding-v4`（1024 维）
- **LangSmith Tracing**：三个 AOP 单点（`tracing.py` 配置激活 + `llm_client.py` LLM 工厂 + `embedding_client.py` Embedding 工厂），零侵入全覆盖。所有 LLM 调用必须走工厂并传入 `LLMTaskName` 枚举值，禁止直接 `ChatOpenAI(...)`。
- **关键约束**：`configure_langsmith_tracing()` 在 `app.py` **模块最顶部**调用（`get_env_var` 有 `lru_cache`）。`get_settings()` 有 `lru_cache`，测试时需 mock 而非 `monkeypatch.setenv`。

### 测试约定

- **pytest.ini**：`asyncio_mode = auto`、`--strict-markers`。注册的 marker：`unit`、`integration`、`slow`、`agent`、`api`。
- **Mock LLM 模式**：`monkeypatch` 或 `unittest.mock.patch` 替换 `build_chat_openai` 返回 `MagicMock`。
- **Mock Settings 模式**：`settings.py` 的 `get_settings()` 有 `lru_cache`，必须 `patch("config.get_settings")` 返回 MagicMock，不能直接 `monkeypatch.setenv`（见 `tests/test_main_agent_memory.py` 的 `mock_settings` fixture）。
- **v1 测试**：`tests/` 下 60 个测试（`-m "not slow"`），覆盖 supervisor、agent、ab_test、hard_constraint_filter、stream_token_markup_parser、tracing 等。

## 重要状态标记

| 组件 | 状态 | 说明 |
|------|------|------|
| `agent/main/` | ✅ 实装 | `build_main_agent()` 工厂 + /chat 端点 |
| `tools/system/*` | ✅ 实装 | `list_available_skills`、`get_current_time` |
| `tools/*` 其余 | ⏳ stub | `raise NotImplementedError`，不要尝试调用 |
| `skills/` Phase 1 | ✅ 实装 | `recommend-courses`、`document-ingestion` |
| `skills/` Phase 2/3 | ⏳ 骨架 | SKILL.md 已就位，agent 逻辑未实装 |
| `api/{chat.py}` | ✅ 实装 | 已注册 |
| `api/{documents,evaluation,report,ppt}.py` | ⏳ 空骨架 | 未注册，curl 404 |
| `agent/{chat,documents,evaluation,report,ppt}/` | ⏳ 预留空包 | 仅有 `__init__.py` docstring |
| `docker-compose.yml` FastGPT 服务 | ⏳ 未启用 | 配置存在但未激活 |

## 常见陷阱

- **任何 Python 代码修改后 Docker 必须 `--build`**：`docker compose up -d --build python-api`
- **`.env` 加载顺序**：仓库根 `.env` → `python/.env`（后者覆盖前者）。Docker 仅注入 `python/.env`。
- **MySQL 宿主机端口**：`localhost:3307` → 容器内 `mysql:3306`。
- **`HTTPX_VERIFY_SSL=false`**：中转站证书 SAN 不匹配时必须在 `.env` 设置。
- **HardConstraintFilter 类别匹配是纯子串**：`"理工"` 不匹配 `"自然科学与工程技术"`，需在 `student_profile_agent.py:190` 和 `hard_constraint_filter.py:201` 加别名映射。
- **FeasibilityAgent LLM 失败是静默的**：`_parse_advice_json()` 返回空 dict → 规则兜底，不抛异常。排查搜索 `llm_advice_parse_empty` 或 `llm_advice_failed`。
- **FeasibilityAgent 最多送 12 门课给 LLM**（`max_tokens=4096`），超 12 门仅走规则兜底。
- **语义缓存阈值 0.95**（1024 维向量对句式模板相似但关键词不同的查询区分度不足）。
- **无 CI/CD**（无 `.github/workflows`），**前端无 lint/test/format 脚本**。
- **`_score_candidates` 接受但不使用 `profile` 参数**是有意为之（广度 vs 精度分离）。



### SSE 流挂起 / 响应体为空（前端报 network error）

特征：接口返回 **200**、没有 5xx，但响应体长时间为空，前端等到超时后报 network error。

第一步看日志，**务必滤掉前端 15s 一次的 health 轮询噪声**，否则业务日志会被完全淹没：

```bash
docker compose logs python-api 2>&1 | grep -v "GET /health HTTP" | tail -80
```

第二步给 SSE 事件打相对时间戳，定位具体是哪一段没有输出：

```bash
start=$(date +%s)
curl -sS -N --max-time 75 -X POST http://127.0.0.1:8000/api/v1/recommend/stream \
  -H "Content-Type: application/json" \
  -d '{"user_id":"debug","prompt":"想选好过的公选课，南校区，最好不要考试","num_items":5,"mode":"pipeline"}' \
  | grep --line-buffered -E '"phase"|event: done' \
  | while IFS= read -r line; do
      now=$(date +%s); printf 'T+%03ds | %s\n' $((now-start)) "${line:0:130}"
    done
```

排查要点：

- **uvicorn 访问日志在响应完成后才打印**，挂起的 SSE 请求不会留痕，不能据此判断"接口没问题"。
- 推荐链路正常应依次输出 `start → phase1_complete → phase15_complete → phase2_complete → phase3_start → … → done`。
  若卡在 `start` 之后、`phase1_complete` 始终不出现，就是 phase1 的 `gather(画像, 召回)` 没完成。
- 画像 agent 的关键日志是 `student_profile.llm_call.start / .done / .cancelled / .failed`，
  其中 `.done` 的 `latency_ms` 直接给出 LLM 真实耗时（thinking 开启时常达 20~30s）。
- 出现 `agent.cancelled` 表示该 agent 被上游取消（客户端断开 / `wait_for` 超时）。
  注意 `asyncio.CancelledError` 在 Python 3.8+ 继承 `BaseException`，`except Exception` 捕获不到——
  这正是"无任何报错却卡住"的常见成因，现已补日志。

## 文档索引

- `docs/INDEX.md` — 文档总索引。面试材料在 `docs/interview-*.md`，架构细节在 `docs/architecture.md`、`docs/code-walkthrough.md`、`docs/supervisor-main-orchestration.md`。
- `docs/v2.0.0/` — v2.0.0 升级工作区。`plan.md` 是总计划，`notes/` 是设计决策记录，`plans/` 是详细实施 plan。
- `docs/v2.0.0/skills-tools-architecture.md` — 三层架构详细说明。
- `docs/v2.0.0/tools/` — 每个 tool 的独立 .md 说明文档（15 个文件）。
- 每次进入 plan 模式前，先读 `docs/v2.0.0/plan.md` + `notes/` 对应决策。

## 核心文件

| 文件 | 职责 |
|------|------|
| `python/agent/app.py` | FastAPI 入口（`uvicorn agent.app:app`） |
| `python/agent/runtime.py` | 运行时单例容器（supervisor/repos/ab_engine/metrics/tool_registry/main_agent） |
| `python/agent/main/agent.py` | `build_main_agent()` 工厂（deepagents 编排） |
| `python/agent/main/prompt.py` | `MAIN_AGENT_SYSTEM_PROMPT`（意图识别 + skill 路由指导） |
| `python/api/chat.py` | `POST /api/v1/chat` 端点 |
| `python/api/recommend.py` | `/api/v1/recommend*` 路由 |
| `python/config/settings.py` | 所有配置（字段名即变量名，双 `.env` 加载） |
| `python/agent/recommend/supervisor.py` | 核心编排（~940 行，Pipeline + ReAct 双模式） |
| `python/agent/recommend/react_tools.py` | 7 个 ReAct 工具定义 |
| `python/agent/recommend/hard_constraint_filter.py` | 确定性硬约束过滤 |
| `python/agent/recommend/agents/` | 5 个 Agent（student_profile/course_recall/course_rerank/course_feasibility/recommendation_reason） |
| `python/ai/llm_client.py` | LLM 工厂（所有 LLM 调用唯一入口） |
| `python/ai/tracing.py` | LangSmith 配置激活层 |
| `python/ai/embedding_client.py` | Embedding 工厂 |
| `python/ai/llm_task_name.py` | LLM/Embedding 场景名称枚举 |
| `python/tools/registry.py` | ToolRegistry（`get_all`/`register_many`/allowlist） |
| `models/schemas.py` | Pydantic 模型 |
| `experiment/ab_test.py` | A/B 分桶 + Thompson Sampling |
| `storage/` | MySQL/Milvus/Redis/MinIO 存储层 |

## 参考项目（`E:\Agent\` 下）

- `E:\Agent\pi` — agent harness/compaction/skills（TS，原生工具，无 MCP）
- `E:\Agent\claude-code` — AgentTool/subagent/autoCompact/circuit breaker/MCPTool（TS，原生+MCP 混合）
- `E:\Agent\OpenMAIC` — call_agent 委派/allowlist gate/PPTX 渲染（TS，Vercel AI SDK + pi-agent-core）
- `E:\Agent\FastGPT` — mcp_server/KB/工作流编排/Code 节点（TS，MCP server+client）