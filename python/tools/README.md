# python/tools/ — 工具注册层与原子能力目录

## 分层

| 层 | 目录 | 内容 |
|----|------|------|
| 工具注册层 | `tools/` | ToolRegistry、CircuitBreaker、MCPClient |
| 系统工具 | `tools/system/` | `get_current_time`、`list_available_skills` |
| 对话工具 | `tools/chat/` | `writing_assistant`、`web_search` |
| 文档工具 | `tools/documents/` | 文档解析 + 分块 + 个人数据脱敏 |
| 知识检索工具 | `tools/knowledge/` | `query_handbook` + `query_transcript`（v0.9 拆分：手册 vs 个人成绩单） |
| 推荐工具 | `tools/recommend/` | 推荐课程 tool |
| 图片工具 | `tools/image/` | 图片生成 |
| 代码工具 | `tools/code/` | 代码执行 |
| 脑图工具 | `tools/mindmap/` | 思维导图生成 |
| 报告工具 | `tools/report/` | 成绩统计 |

## 工具索引

| 工具 | 状态 | Phase | 类别 | 位置 |
|------|------|-------|------|------|
| `list_available_skills` | `implemented` | 1 | `system/*` | `tools/system/list_available_skills.py` |
| `get_current_time` | `implemented` | 1 | `system/*` | `tools/system/get_current_time.py` |
| `recommend_courses` | `implemented` | 1 | `recommend/*` | `tools/recommend/recommend_courses.py`（内部走统一流式，ReAct→Pipeline 兜底） |
| `writing_assistant` | `stub` | 1 | `chat/*` | `tools/chat/writing_assistant.py` |
| `web_search` | `stub` | 3 | `chat/*` | `tools/chat/web_search.py` |
| `image_generate` | `stub` | 3/4 | `image/*` | `tools/image/image_generate.py` |
| `code_interpreter` | `stub` | 3/4 | `code/*` | `tools/code/code_interpreter.py` |
| `mindmap_generator` | `stub` | 3/4 | `mindmap/*` | `tools/mindmap/mindmap_generator.py` |
| `compute_weighted_grade` | `stub` | 2 | `report/*` | `tools/report/compute_weighted_grade.py` |
| `parse_document` | `implemented` | 1 | `documents/*` | `tools/documents/parser.py` |
| `chunk_document` | `implemented` | 1 | `documents/*` | `tools/documents/chunker.py` |
| `adaptive_knowledge_retrieve` | `implemented` | 4 | `knowledge/*` | `tools/knowledge/adaptive_retrieve.py`（LLM planner + dense/lexical + RRF + fragment rerank，主 Agent 唯一高层 RAG 入口） |
| `query_handbook` | `implemented` | 1 | `knowledge/*` | `tools/knowledge/query_handbook.py`（手册检索，user_id=public 分区，top_k 默认 5） |
| `query_transcript` | `implemented` | 1 | `knowledge/*` | `tools/knowledge/query_transcript.py`（个人成绩单，仅本人 user_id 分区，top_k 默认 3，强权限隔离） |
| `desensitize_transcript` | `implemented` | 1 | `documents/*` | `tools/documents/desensitizer.py` |

## 基础设施

| 组件 | 状态 | Phase | 位置 |
|------|------|-------|------|
| `ToolRegistry` | `implemented` | 1 | `tools/registry.py` |
| `CircuitBreaker` | `implemented` | 1 | `tools/circuit_breaker.py` |
| `MultiServerMCPClient` | `stub` | 3 | `tools/mcp_client.py` |

## 文档

每个 tool 的详细说明文档位于 `docs/v2.0.0/tools/` 目录下，与 .py 文件同名（`.md` 后缀）。

## 与 skills/ 的区别

| | `tools/`（原子能力） | `skills/`（技能文档） |
|---|---|---|
| 内容 | Python `@tool` 代码 | SKILL.md 说明文档 |
| 注册 | ToolRegistry | SkillsMiddleware 自动扫描 |
| 使用 | Agent 直接调用执行 | Agent 阅读后按步骤执行 |
| 机制 | 工具调用（tool call） | 渐进式披露（先摘要后全文） |

## 注册方式

所有工具在 `runtime.init()` 中通过 `ToolRegistry.register_many()` 批量注册，见 `python/agent/runtime.py`。主 agent 从 `tool_registry.get_all()` 获取工具列表。
