# -*- coding: utf-8 -*-
"""v2 工具实现包 — 原子能力目录。

tools/ 放原子能力（解析、分块、向量化、渲染、搜索、插件），
以 @tool 装饰器 + Pydantic args_schema 暴露，通过 ToolRegistry 统一注册。

分层（按功能域子包组织）：
  - tools/           — 注册层（ToolRegistry/CircuitBreaker/MCPClient）
  - tools/system/    — 系统级工具（get_current_time, list_available_skills）
  - tools/chat/      — 对话工具（writing_assistant, web_search）
  - tools/documents/ — 文档解析 + 分块
  - tools/recommend/ — 推荐工具
  - tools/image/     — 图片生成
  - tools/code/      — 代码执行
  - tools/mindmap/   — 脑图生成
  - tools/report/    — 报告统计

与 skills/ 的区别：
  - tools/ = Python @tool 代码，原子能力，ToolRegistry 注册
  - skills/ = SKILL.md 文档，技能说明，SkillsMiddleware 注入 system prompt

架构决策：
  - 每个工具一个文件，通过子包 __init__.py 逐级导出，最终统一到 tools/__init__.py
  - 工具用 @tool 装饰器 + Pydantic args_schema，LangChain 自动生成 JSON Schema
  - 注册走 ToolRegistry（tools/registry.py），不直接 import
"""

from __future__ import annotations

# ── 工具注册层 ────────────────────────────────────────────────────────
from .circuit_breaker import CircuitBreaker
from .mcp_client import MultiServerMCPClient, get_mcp_client
from .registry import ToolRegistry, get_registry

# ── 功能域子包（工具逐级导出） ─────────────────────────────────────────
from .system import dispatch_module, get_current_time, list_available_skills
from .chat import web_search, writing_assistant
from .documents import chunk_document, parse_document
from .knowledge import adaptive_knowledge_retrieve, query_handbook, query_transcript
from .evaluation import (
    compute_radar_values,
    design_dimensions,
    generate_comment,
    get_academic_snapshot,
)
from .recommend import (
    check_feasibility,
    extract_profile,
    filter_hard_constraints,
    generate_reasons,
    recommend_courses,
    rerank_courses,
    search_courses,
    semantic_filter_courses,
)
from .image import image_generate, image_generate_get, image_recognize
from .code import code_interpreter
from .mindmap import mindmap_generator
from .report import (
    compute_weighted_grade,
    inspect_score_excels,
    render_report_batch,
)

# ── 公开 API ──────────────────────────────────────────────────────────
__all__ = [
    # 注册层
    "ToolRegistry",
    "get_registry",
    "CircuitBreaker",
    "MultiServerMCPClient",
    "get_mcp_client",
    # 工具
    "dispatch_module",
    "get_current_time",
    "list_available_skills",
    "writing_assistant",
    "web_search",
    "parse_document",
    "chunk_document",
    # query_knowledge 在 2026-08-25 重构中删除，被 query_handbook / query_transcript 替代
    "adaptive_knowledge_retrieve",
    "query_handbook",
    "query_transcript",
    "recommend_courses",
    "extract_profile",
    "search_courses",
    "filter_hard_constraints",
    "semantic_filter_courses",
    "rerank_courses",
    "check_feasibility",
    "generate_reasons",
    "image_generate",
    "image_generate_get",
    "image_recognize",
    "code_interpreter",
    "mindmap_generator",
    "compute_weighted_grade",
    "inspect_score_excels",
    "render_report_batch",
    "get_academic_snapshot",
    "design_dimensions",
    "compute_radar_values",
    "generate_comment",
]