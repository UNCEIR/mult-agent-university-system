"""运行时单例容器。

管理所有全局单例（supervisor/repos/ab_engine/metrics/tool_registry）的生命周期。
ToolRegistry 在 build_main_agent 之前初始化，确保主 agent 能从注册中心获取工具。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from agent.recommend.supervisor import SupervisorOrchestrator
from experiment.ab_test import ABTestEngine
from observability.metrics import MetricsCollector

logger = structlog.get_logger()

# 全局单例（初始化后赋值）
ab_engine: ABTestEngine | None = None
metrics_collector: MetricsCollector | None = None
supervisor: SupervisorOrchestrator | None = None
mysql_repo: Any = None
redis_repo: Any = None
course_vector_repo: Any = None

# v2.0.0 主 agent 单例
main_agent: Any = None

# v2.0.0 ToolRegistry 单例
tool_registry: Any = None

# v2.0.0 知识库（学生手册/个人成绩单）仓储
document_vector_repo: Any = None
document_repo: Any = None

# Phase 2：report 产物存储与元数据；evaluation 评价档案
minio_repo: Any = None
report_artifact_repo: Any = None
report_upload_repo: Any = None
evaluation_repo: Any = None
chat_session_repo: Any = None
image_asset_service: Any = None


async def init() -> None:
    """初始化所有运行时单例。在 lifespan 启动时调用。"""
    global ab_engine, metrics_collector, supervisor
    global mysql_repo, redis_repo, course_vector_repo
    global main_agent, tool_registry
    global document_vector_repo, document_repo
    global minio_repo, report_artifact_repo, report_upload_repo, evaluation_repo, chat_session_repo, image_asset_service
    from ai.embedding_client import build_embedding_client
    from ai.llm_task_name import LLMTaskName
    from storage.milvus.course_vector_repo import CourseVectorRepository
    from storage.milvus.document_vector_repo import DocumentVectorRepository
    from storage.mysql.base import MySQLRepository
    from storage.mysql.document_repo import DocumentRepository
    from storage.redis.feature_repo import RedisFeatureRepository

    # ── v1 单例 ──────────────────────────────────────────────────────
    ab_engine = ABTestEngine()
    metrics_collector = MetricsCollector()
    supervisor = SupervisorOrchestrator(ab_engine=ab_engine)
    mysql_repo = MySQLRepository()
    redis_repo = RedisFeatureRepository()
    course_vector_repo = CourseVectorRepository(build_embedding_client(task_name=LLMTaskName.COURSE_RECALL))

    # ── v2.0.0 知识库仓储（学生手册/个人成绩单） ─────────────────────
    document_vector_repo = DocumentVectorRepository(
        build_embedding_client(task_name=LLMTaskName.QUERY_KNOWLEDGE)
    )
    document_repo = DocumentRepository()

    # ── Phase 2：report 产物存储（MinIO + 本地兜底）与元数据 ─────────
    from config import get_settings as _get_settings
    from storage.minio.minio_repo import MinioRepository
    from storage.mysql.report_artifact_repo import ReportArtifactRepository

    _s = _get_settings()
    minio_repo = MinioRepository(
        endpoint=_s.minio_endpoint,
        port=_s.minio_port,
        access_key=_s.minio_access_key,
        secret_key=_s.minio_secret_key,
        secure=_s.minio_secure,
        bucket=_s.minio_report_bucket,
        connect_timeout=_s.minio_connect_timeout,
    )
    report_artifact_repo = ReportArtifactRepository()
    from storage.mysql.report_upload_repo import ReportUploadRepository
    report_upload_repo = ReportUploadRepository()
    from storage.mysql.evaluation_repo import EvaluationRepository
    from storage.mysql.chat_session_repo import ChatSessionRepository

    evaluation_repo = EvaluationRepository()
    chat_session_repo = ChatSessionRepository()
    # 2026-09-21：私有聊天图片资产（独立 bucket/local fallback + MySQL 元数据 sidecar fallback）
    from agent.images.service import configure_image_asset_service
    from storage.mysql.chat_attachment_repo import ChatAttachmentRepository

    _chat_image_root = Path(__file__).resolve().parent.parent / ".documents" / "chat_images"
    _chat_minio = MinioRepository(
        endpoint=_s.minio_endpoint,
        port=_s.minio_port,
        access_key=_s.minio_access_key,
        secret_key=_s.minio_secret_key,
        secure=_s.minio_secure,
        bucket=_s.minio_chat_bucket,
        connect_timeout=_s.minio_connect_timeout,
        local_root=_chat_image_root / "objects",
    )
    image_asset_service = configure_image_asset_service(
        metadata_repo=ChatAttachmentRepository(),
        minio_repo=_chat_minio,
        fallback_root=_chat_image_root,
        ttl_days=_s.vision_ttl_days,
    )

    # ── v2.0.0 ToolRegistry（必须在 build_main_agent 之前初始化） ─────
    from tools import (
        ToolRegistry,
        adaptive_knowledge_retrieve,
        check_feasibility,
        code_interpreter,
        compute_weighted_grade,
        dispatch_module,
        extract_profile,
        filter_hard_constraints,
        generate_reasons,
        get_current_time,
        image_generate,
        image_generate_get,
        image_recognize,
        inspect_score_excels,
        list_available_skills,
        mindmap_generator,
        query_handbook,
        query_transcript,
        recommend_courses,
        render_report_batch,
        rerank_courses,
        search_courses,
        semantic_filter_courses,
        web_search,
        writing_assistant,
    )
    from tools.evaluation import get_academic_snapshot
    from tools.evaluation.tool_wrappers import compute_radar_values, design_dimensions, generate_comment
    from tools.image import image_recognize

    tool_registry = ToolRegistry()
    tool_registry.register_many([
        list_available_skills,
        get_current_time,
        dispatch_module,
        recommend_courses,
        extract_profile,
        search_courses,
        filter_hard_constraints,
        semantic_filter_courses,
        rerank_courses,
        check_feasibility,
        generate_reasons,
        writing_assistant,
        web_search,
        image_generate,
        image_generate_get,
        code_interpreter,
        mindmap_generator,
        compute_weighted_grade,
        adaptive_knowledge_retrieve,
        query_handbook,
        query_transcript,
        inspect_score_excels,
        render_report_batch,
        get_academic_snapshot,
        image_recognize,
        design_dimensions,
        compute_radar_values,
        generate_comment,
    ])
    # 注册子包 tool（documents/）
    try:
        from tools.documents import chunk_document, parse_document
        tool_registry.register_many([parse_document, chunk_document])
    except Exception:
        logger.warning("runtime.init.documents_tools_not_available")

    # ── v2.0.0 主 agent（deepagents 记忆 + 意图识别 + 渐进式 skill） ──
    # 主 agent 工具白名单由 MAIN_AGENT_SPEC.allowed_tools 声明（specs.py），
    # factory 在 tools=None 时从 registry 按白名单取；此处不传 tools。
    from agent.main import build_business_subagents, build_main_agent

    # 子 agent 实例挂载（skill 非空心化）：预编译 recommend/report/evaluation/ppt
    # 业务子 agent（各带 spec.skills=SKILL.md + allowed_tools 业务工具），作为
    # CompiledSubAgent 传给 main_agent；deepagents 据此给主 agent 注入 task() 委派
    # 工具——教师端"出报告/写评语"类意图可委派子 agent 读 SKILL.md 走真实流程，
    # 而非只返回模块名引导跳页。
    business_subagents = await build_business_subagents()
    main_agent = await build_main_agent(subagents=business_subagents)

    # 2026-08-25：DocumentsPage 上传链路 —— DocumentIngestionService 在
    # api/documents.py 模块级 new 时没传 repos，导致 vector_repo / embedding_client /
    # document_repo 永远是 None，service.ingest() 的 Milvus/MySQL 写入段从未执行；
    # 这里在 lifespan 启动后一次性把已构造好的 repos 注入 service 模块级单例。
    from api.documents import service as _documents_service

    _documents_service.set_repos(
        vector_repo=document_vector_repo,
        document_repo=document_repo,
        embedding_client=document_vector_repo.embedding_client,
    )

    logger.info(
        "runtime.init",
        supervisor=supervisor is not None,
        main_agent=main_agent is not None,
        tool_registry_tools=len(tool_registry.list_tools()),
        document_vector_repo=document_vector_repo is not None,
        documents_service_repos_wired=_documents_service.vector_repo is not None,
    )


def shutdown() -> None:
    """清理运行时资源。在 lifespan 关闭时调用。

    当前无外部连接需显式关闭：AsyncSqliteSaver 内部管理连接池，
    MySQL/Redis/Milvus/embedding 客户端由进程退出时回收。
    若未来引入需要显式关闭的资源（如长连接池），在此补充。
    """
    logger.info("runtime.shutdown")
