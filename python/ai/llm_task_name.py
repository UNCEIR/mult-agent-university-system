from __future__ import annotations

from enum import Enum


class LLMTaskName(str, Enum):
    """LLM/Embedding 调用场景名称枚举。

    每个枚举值对应一个调用场景，会作为 ``run_name`` 传递到 LangSmith trace，
    替代默认的类名，便于在监控平台按业务维度识别调用。
    """

    # ── LLM 场景 ────────────────────────────────────────────────────
    STUDENT_PROFILE = "student_profile"
    COURSE_RERANK = "course_rerank"
    COURSE_FEASIBILITY = "course_feasibility"
    RECOMMENDATION_REASON = "recommendation_reason"
    SEMANTIC_FILTER = "semantic_filter"
    REACT_ORCHESTRATOR = "react_orchestrator"
    GRAPH_SEMANTIC_FILTER = "graph_semantic_filter"

    # ── v2.0.0 主 Agent 场景 ──────────────────────────────────────────
    MAIN_AGENT_ROUTER = "main_agent_router"  # 主 agent 意图识别 + 路由
    RECOMMEND_COURSES_TOOL = "recommend_courses_tool"  # 推荐场景 agent
    TRANSCRIPT_PARSER = "transcript_parser"  # 成绩报告场景 agent
    EVALUATION_GENERATOR = "evaluation_generator"  # 评价寄语场景 agent
    CHAT_SUMMARY = "chat_summary"  # 对话摘要（compaction 用）
    CHAT_ENDPOINT = "chat_endpoint"  # /api/v1/chat 端点调用
    MAIN_AGENT_BUILD = "main_agent_build"  # 主 agent 工厂构建
    QUERY_KNOWLEDGE = "query_knowledge"  # 知识库检索（学生手册/个人成绩单）
    DOCUMENTS_UPLOAD = "documents_upload"  # 文档摄入向量化

    # ── Phase 2：report / evaluation / 记忆 / 视觉 ────────────────────
    REPORT_HTML_FILL = "report_html_fill"  # report LLM 模板填充（复用 llm_model）
    REPORT_SUBJECTIVE_EVAL = "report_subjective_eval"  # report 综合评语
    EVALUATION_DIMENSION_DESIGN = "evaluation_dimension_design"  # evaluation 维度提案
    MEMORY_EXTRACT = "memory_extract"  # chat 跨会话记忆提取（增量摘要）
    EVAL_JUDGE = "eval_judge"  # LLM-as-judge（faithfulness/answer_relevancy/rubric，Phase 4 B1）
    VISION_ANALYZE = "vision_analyze"  # image_recognize 视觉分析（qwen3-vl-plus）
    RETRIEVAL_PLANNER = "retrieval_planner"  # Agentic RAG 查询/知识库/策略规划
    RETRIEVAL_RERANK = "retrieval_rerank"  # Agentic RAG 候选片段 listwise 筛选

    # ── Embedding 场景 ──────────────────────────────────────────────
    COURSE_RECALL = "course_recall"  # 在线召回/搜索（course_recall_agent + main.py 探活）
    BACKFILL = "backfill"  # 离线批量回填（backfill_milvus_vectors.py）
