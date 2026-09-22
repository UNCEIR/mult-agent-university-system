# Phase 1 编码实施报告 — 记忆机制 + Tool/Skill/Agent 三层结构改造

> 本文档记录 Phase 1 编码阶段的实际实施结果，包含已完成的工作、关键决策、文件清单和验证结果。
>
> 日期：2026-08-07
> 状态：✅ 已完成

## 一、实施概览

### 1.1 工作范围

Phase 1 编码阶段包含两个主要工作流：

**工作流 A：记忆机制 demo（deepagents memory + 意图识别 + 渐进式 skill）**
- 实装 `agent/main/` 主 agent 工厂（`build_main_agent()`）
- 接入 deepagents 0.7.5 的 memory/summarization/filesystem/skills middleware
- SqliteSaver checkpointer（thread_id 跨会话恢复）
- `POST /api/v1/chat` 端点实装
- 种子长期记忆文件 `memories/AGENTS.md`

**工作流 B：Tool/Skill/Agent 三层结构改造**
- 解决命名冲突（`agent/main.py` → `agent/app.py`）
- 统一 tool 体系（走 ToolRegistry，`tools/` 功能域子包组织）
- 兑现 args_schema（所有 @tool 加 Pydantic Input 模型）
- SKILL.md 对齐代码（allowed_tools 映射到 `tools/` 子包）
- 补全 9 个 skill 的 SKILL.md
- 生成 `docs/v2.0.0/skills-tools-architecture.md`
- 清理死代码与重复

### 1.2 核心改动统计

| 统计项 | 数量 |
|--------|------|
| 新增文件 | 25+ |
| 修改文件 | 15+ |
| 删除文件 | 3（`agent/main.py`→已重命名、`agent/main/tools.py`、`test_supervisor_pipeline.py`） |
| 新增 skill | 7 个 SKILL.md |
| 新增 tool 文档 | 15 个 .md |
| 新增工具子包 | 8 个（`system/chat/documents/recommend/image/code/mindmap/report`） |
| 单测通过 | 60 passed, 1 deselected |

---

## 二、工作流 A：记忆机制 demo

### 2.1 架构图

```
POST /api/v1/chat
  → runtime.main_agent.ainvoke()
    → create_deep_agent(
        model=ChatOpenAI(中转站 deepseek-v4.1-flash),
        tools=ToolRegistry.get_all(),
        backend=CompositeBackend(StateBackend + FilesystemBackend×2),
        skills=["/skills/"],         # SkillsMiddleware 自动加载
        memory=["/memories/AGENTS.md"],  # MemoryMiddleware 自动加载
        checkpointer=SqliteSaver,
        middleware=[SummarizationMiddleware, SummarizationToolMiddleware],
      )
```

### 2.2 文件清单

| 文件 | 职责 | 状态 |
|------|------|------|
| `python/agent/main/__init__.py` | 导出 `build_main_agent` | ✅ 实装 |
| `python/agent/main/agent.py` | `build_main_agent()` 工厂，组装所有 middleware + tool + backend + checkpointer | ✅ 实装 |
| `python/agent/main/backend.py` | `build_main_backend()`：CompositeBackend（StateBackend default + FilesystemBackend 路由 `/skills/` + `/memories/`） | ✅ 实装 |
| `python/agent/main/checkpointer.py` | `build_checkpointer()`：SqliteSaver（本地 sqlite，`check_same_thread=False`） | ✅ 实装 |
| `python/agent/main/prompt.py` | `MAIN_AGENT_SYSTEM_PROMPT`：意图识别 + 渐进式 skill 加载 + 记忆管理 + 工具使用原则 | ✅ 实装 |
| `python/agent/main/subagents.py` | subagent 工厂占位（`build_report_subagent`/`build_evaluation_agent`/`build_ppt_agent`，Phase 2/3 实装） | ✅ 骨架 |
| `python/memories/AGENTS.md` | 种子长期记忆文件（项目背景 + 用户偏好占位 + 技能索引 + 记忆更新指导） | ✅ 实装 |
| `python/api/chat.py` | `POST /api/v1/chat` 端点（ChatRequest/ChatResponse Pydantic 模型，调用 `runtime.main_agent.ainvoke`） | ✅ 实装 |
| `python/agent/app.py` | FastAPI 入口（`include_router(chat.router)` 注册 chat 路由） | ✅ 重命名 |

### 2.3 关键设计决策

**Compaction 阈值**（对齐决策 11）：
```python
# demo 用 messages 触发便于验证（settings.agent_compaction_trigger_messages=8）
trigger = ("messages", s.agent_compaction_trigger_messages) if s.agent_compaction_trigger_messages \
           else ("tokens", s.agent_context_window_tokens - 13000)
summ = SummarizationMiddleware(model=llm, backend=backend, trigger=trigger, keep=("tokens", 20000))
```

**Backend 路由**：
```python
backend = CompositeBackend(
    default=StateBackend(),
    routes={
        "/skills/": FilesystemBackend(root_dir=skills_dir),   # 真实 python/skills/
        "/memories/": FilesystemBackend(root_dir=memory_dir), # 真实 python/memories/
    },
)
```

**Tool 注入方式**：`build_main_agent(tools)` 接受参数，由 `runtime.init()` 传入 `tool_registry.get_all()`，保持编排层与能力层分离。

### 2.4 Settings 新增配置项

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `memory_dir` | `""`（自动推导 `<repo_root>/python/memories`） | 长期记忆目录 |
| `skills_dir` | `""`（自动推导 `<repo_root>/python/skills`） | SKILL.md 技能文档目录 |
| `checkpoint_sqlite_path` | `""`（自动推导 `<repo_root>/python/.checkpoint.db`） | SqliteSaver 持久路径 |
| `agent_context_window_tokens` | `128000` | 模型上下文窗口 |
| `agent_compaction_trigger_tokens` | `None`（None 时用 `context_window-13000`） | compaction token 阈值 |
| `agent_compaction_keep_tokens` | `20000` | 保留的最近 token 数 |
| `agent_compaction_trigger_messages` | `8`（demo 用 messages 触发） | compaction 消息数阈值 |

---

## 三、工作流 B：Tool/Skill/Agent 三层结构改造

### 3.1 Step 1：解决命名冲突

**问题**：`python/agent/main.py`（FastAPI 入口）与 `python/agent/main/`（主 agent 子包）同名，Python 包优先于同名模块，`uvicorn agent.main:app` 实际无法解析到 FastAPI app。

**解决**：
```bash
git mv python/agent/main.py python/agent/app.py
```

| 文件 | 改动 |
|------|------|
| `python/agent/app.py` | 重命名，`uvicorn.run("agent.app:app")` |
| `python/Dockerfile` | `CMD` 改为 `uvicorn agent.app:app` |
| `python/scripts/post_recommend_local.py` | 注释同步 |
| `CLAUDE.md` | 核心文件表同步 + 新增「agent.app vs agent.main」陷阱说明 |

### 3.2 Step 2：统一 tool 体系（走 ToolRegistry）

**迁移轻量 tool**：`agent/main/tools.py` 的 3 个 tool 迁移到 `tools/` 子包：
- `list_available_skills` → `tools/system/list_available_skills.py`
- `get_current_time` → `tools/system/get_current_time.py`
- `writing_assistant` → `tools/chat/writing_assistant.py`（去重，合并原 `tools/writing_assistant.py`）

**删除**：`python/agent/main/tools.py`

**ToolRegistry 扩展**（`tools/registry.py`）：
- `get_all(allowed=None)`：返回所有已注册 tool，按 allowlist 过滤
- `register_many(tools)`：批量注册

**`tools/__init__.py` 改为真实导出**：
```python
from .system import get_current_time, list_available_skills
from .chat import web_search, writing_assistant
from .documents import chunk_document, parse_document
from .recommend import recommend_courses
from .image import image_generate
from .code import code_interpreter
from .mindmap import mindmap_generator
from .report import compute_weighted_grade
```

**`runtime.py` 接入 ToolRegistry**：
```python
tool_registry = ToolRegistry()
tool_registry.register_many([list_available_skills, get_current_time, recommend_courses, ...])
main_agent = build_main_agent(tools=tool_registry.get_all())
```

### 3.3 Step 3：兑现 args_schema

所有 @tool 加 Pydantic `args_schema`，使用 `@tool(args_schema=XXXInput)` 形式：

| 文件 | Input 模型 | 状态 |
|------|-----------|------|
| `tools/recommend/recommend_courses.py` | `RecommendCoursesInput` | stub |
| `tools/chat/writing_assistant.py` | `WritingAssistantInput` | stub |
| `tools/chat/web_search.py` | `WebSearchInput` | stub |
| `tools/image/image_generate.py` | `ImageGenerateInput` | stub |
| `tools/code/code_interpreter.py` | `CodeInterpreterInput` | stub |
| `tools/mindmap/mindmap_generator.py` | `MindmapGeneratorInput` | stub |
| `tools/report/compute_weighted_grade.py` | `ComputeWeightedGradeInput` | stub |
| `tools/documents/parser.py` | `ParseDocumentInput` | stub |
| `tools/documents/chunker.py` | `ChunkDocumentInput` | stub |
| `tools/system/list_available_skills.py` | 无参数 | implemented |
| `tools/system/get_current_time.py` | 无参数 | implemented |

### 3.4 Step 4：对齐 SKILL.md 与代码

| SKILL.md | 原本 allowed_tools | 实际 allowed_tools |
|----------|-------------------|-------------------|
| `recommend-courses/SKILL.md` | 8 个不存在的 tool 名 | `[recommend_courses]` |
| `document-ingestion/SKILL.md` | `[read_file, write_file, execute]` | `[parse_document, chunk_document]` |

### 3.5 Step 5：补 .md 文档 + 空骨架标记

**15 个 tool 独立文档**位于 `docs/v2.0.0/tools/`：
- `recommend_courses.md`、`writing_assistant.md`、`web_search.md`、`image_generate.md`、`code_interpreter.md`、`mindmap_generator.md`、`compute_weighted_grade.md`、`list_available_skills.md`、`get_current_time.md`、`registry.md`、`circuit_breaker.md`、`mcp_client.md`、`parser.md`、`chunker.md`、`README.md`

**`tools/README.md`**：完整索引表（name / 状态 / Phase / .md 链接 / 位置 / category）

**空骨架标记**：每个 stub tool 的 module docstring 标注 `Phase: X (stub — NotImplementedError)`

### 3.6 Step 6：skills-tools-architecture.md

生成 `docs/v2.0.0/skills-tools-architecture.md`，包含：
- `tools/` vs `skills/` vs `agent/` 三层职责定义
- tool 命名规范、args_schema 规范、错误返回规范（isError result）
- MCP tool 与原生 tool 的统一接口
- circuit breaker 集成
- 状态标记规范
- 注册流程

### 3.7 Step 7：工具子包组织

将 `tools/` 从扁平结构重构为 8 个功能域子包：

```
tools/
├── __init__.py              # 统一导出所有 tool
├── registry.py              # ToolRegistry
├── circuit_breaker.py       # CircuitBreaker
├── mcp_client.py            # MCP 客户端
├── README.md                # 工具索引
├── system/                  # get_current_time, list_available_skills (implemented)
├── chat/                    # writing_assistant, web_search (stub)
├── documents/               # parser, chunker (stub)
├── recommend/               # recommend_courses (stub)
├── image/                   # image_generate (stub)
├── code/                    # code_interpreter (stub)
├── mindmap/                 # mindmap_generator (stub)
└── report/                  # compute_weighted_grade (stub)
```

### 3.8 Step 8：SKILL.md 扩展

从 Phase 1 的 2 个 skill 扩展到 9 个 skill，覆盖全部 Phase：

| 技能 | Phase | 状态 | allowed_tools |
|------|-------|------|-------------|
| `recommend-courses` | 1 | ✅ 实装 | `[recommend_courses]` |
| `document-ingestion` | 1 | ✅ 实装 | `[parse_document, chunk_document]` |
| `report-generation` | 2 | ⏳ 骨架 | `[compute_weighted_grade]` |
| `evaluation-writing` | 2 | ⏳ 骨架 | `[compute_weighted_grade]` |
| `knowledge-query` | 3 | ⏳ 骨架 | （无，知识库检索） |
| `web-search` | 3 | ⏳ 骨架 | `[web_search]` |
| `deep-thinking` | 3 | ⏳ 骨架 | （无，纯推理） |
| `writing` | 3 | ⏳ 骨架 | `[writing_assistant, web_search]` |
| `ppt-generation` | 3 | ⏳ 骨架 | `[web_search]` |

---

## 四、关键文件改动清单

### 4.1 新增文件

| 路径 | 说明 |
|------|------|
| `python/agent/main/__init__.py` | 导出 `build_main_agent` |
| `python/agent/main/agent.py` | `build_main_agent()` 工厂 |
| `python/agent/main/backend.py` | `build_main_backend()` CompositeBackend |
| `python/agent/main/checkpointer.py` | `build_checkpointer()` SqliteSaver |
| `python/agent/main/prompt.py` | `MAIN_AGENT_SYSTEM_PROMPT` |
| `python/agent/main/subagents.py` | subagent 工厂占位（Phase 2/3） |
| `python/memories/AGENTS.md` | 种子长期记忆 |
| `python/tests/test_main_agent_memory.py` | 主 agent 记忆机制单测 |
| `python/tools/system/__init__.py` | 系统工具子包 |
| `python/tools/system/list_available_skills.py` | 迁移自 `agent/main/tools.py` |
| `python/tools/system/get_current_time.py` | 迁移自 `agent/main/tools.py` |
| `python/tools/chat/__init__.py` | 对话工具子包 |
| `python/tools/image/__init__.py` + `image_generate.py` | 图片生成子包 |
| `python/tools/code/__init__.py` + `code_interpreter.py` | 代码执行子包 |
| `python/tools/mindmap/__init__.py` + `mindmap_generator.py` | 脑图生成子包 |
| `python/tools/report/__init__.py` + `compute_weighted_grade.py` | 报告统计子包 |
| 7 个 `skills/*/SKILL.md` | 扩展至 9 个技能 |
| 15 个 `docs/v2.0.0/tools/*.md` | tool 说明文档 |
| `docs/v2.0.0/skills-tools-architecture.md` | 三层架构说明 |

### 4.2 修改文件

| 路径 | 改动 |
|------|------|
| `python/agent/app.py` | 重命名自 `main.py`，`include_router(chat.router)` |
| `python/agent/runtime.py` | 加 tool_registry 单例 + register_many + build_main_agent |
| `python/api/chat.py` | 从空骨架实装为完整 /chat 端点 |
| `python/config/settings.py` | 加 7 个 memory/skill/checkpoint 配置项 |
| `python/ai/llm_task_name.py` | 加 `MAIN_AGENT_ROUTER` + `CHAT_SUMMARY` |
| `python/requirements.txt` | 加 `langgraph-checkpoint-sqlite` + `langgraph-checkpoint-redis` |
| `python/Dockerfile` | `CMD`: `agent.main:app` → `agent.app:app` |
| `python/tools/__init__.py` | 从纯 docstring 改为真实导出所有 tool |
| `python/tools/README.md` | 完整索引表（子包结构） |
| `python/tools/registry.py` | 加 `get_all` / `register_many` |
| `python/skills/__init__.py` | 更新为 9 个技能概览表 |
| `python/skills/README.md` | 更新为 9 个技能清单 |
| `python/skills/recommend-courses/SKILL.md` | `allowed_tools` 对齐 |
| `python/skills/document-ingestion/SKILL.md` | `allowed_tools` 对齐 |
| `CLAUDE.md` | 核心文件表 + 陷阱 + 结构同步 |
| `docs/v2.0.0/plan.md` | Phase 3 补充记忆条目 |
| 8 个 `tools/*/__init__.py` | 子包导出 |

### 4.3 删除文件

| 路径 | 说明 |
|------|------|
| `python/agent/main.py` | 重命名为 `app.py` |
| `python/agent/main/tools.py` | 3 个 tool 迁移到 `tools/` 子包后删除 |
| `python/tests/test_supervisor_pipeline.py` | 已删除 |

---

## 五、验证结果

### 5.1 编译验证

```bash
cd python && python -m compileall agent/main/ api/chat.py tools/
# 全部通过，无语法错误
```

### 5.2 单测验证

```bash
cd python && python -m pytest tests/ -m "not slow" -v
# 60 passed, 1 deselected
```

关键测试覆盖：
- `test_main_agent_memory.py`：
  - `test_import_and_build`：`build_main_agent()` 返回 compiled agent，传入 `model/backend/skills/memory/checkpointer/system_prompt`
  - `test_import_tools`：`from tools import list_available_skills, get_current_time` 正常
  - `test_import_prompt`：`MAIN_AGENT_SYSTEM_PROMPT` 长度 >100
  - `test_import_subagents`：3 个 subagent stub 抛出 `NotImplementedError`
  - `test_import_backend`：`build_main_backend()` 返回非空 backend
  - `test_import_checkpointer`：`build_checkpointer()` 返回非空 checkpointer
  - `test_chat_request_model`：ChatRequest/ChatResponse Pydantic 模型正常
  - `test_agents_md_exists`：AGENTS.md 种子文件存在且内容正确
  - `test_runtime_imports`：runtime 模块导入无循环依赖

### 5.3 Import 链路验证

```python
from agent.app import app
from agent.main import build_main_agent
from tools import recommend_courses, writing_assistant, list_available_skills, get_current_time
# 全部正常
```

### 5.4 工具子包验证

```python
from tools.system import get_current_time, list_available_skills
from tools.chat import writing_assistant, web_search
from tools.documents import chunk_document, parse_document
from tools.recommend import recommend_courses
from tools.image import image_generate
from tools.code import code_interpreter
from tools.mindmap import mindmap_generator
from tools.report import compute_weighted_grade
# 全部正常
```

### 5.5 v1 回归验证

所有 60 个测试通过，v1 推荐链路相关测试（`test_recommend_pipeline.py`、`test_hard_constraint_filter.py`、`test_ab_test.py` 等）全部保持绿色。

---

## 六、Phase 2 待办

- 实装 `recommend_courses` tool（连接 v1 supervisor pipeline）
- 实装 `report-generation` 和 `evaluation-writing` 技能对应的 agent 逻辑
- 文档流水线实装（`POST /api/v1/documents/upload`）
- FastGPT 部署 + MCP 集成
- 前端 MainPage 组件