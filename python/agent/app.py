"""
大学校园多智能体平台 — FastAPI Entry Point

Endpoints:
  POST /api/v1/chat               - 主 agent 统一会话（多轮对话 + 记忆管理 + 意图识别）
  POST /api/v1/chat/stream        - SSE 流式主 agent 会话（token/tool/done/error 事件）
  POST /api/v1/recommend/stream   - SSE 流式推荐（默认 ReAct → 兜底 Pipeline，统一入口）
  POST /api/v1/documents/upload   - 文档摄入（知识库）
  GET  /api/v1/experiments        - 查看A/B实验状态
  GET  /api/v1/metrics            - 查看系统监控指标
  GET  /api/v1/health             - 健康检查（与前端 /api 前缀一致）
  GET  /health                    - 健康检查（运维探活常用路径）
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


from ai.tracing import configure_langsmith_tracing

configure_langsmith_tracing()

from contextlib import asynccontextmanager
from urllib.parse import urlparse

import structlog
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agent import runtime
from api import recommend, health, chat, chat_images, documents, report, evaluation, auth, metrics, images
from config import get_settings

logger = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _assert_startup_config()
    await runtime.init()
    llm_parsed = urlparse(settings.llm_base_url)
    logger.info(
        "app.startup",
        model=settings.llm_model,
        llm_api_host=llm_parsed.netloc or llm_parsed.path,
    )
    yield
    runtime.shutdown()
    logger.info("app.shutdown")


app = FastAPI(
    title="University Campus Multi-Agent Platform",
    description="大学校园多智能体平台",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 统一响应信封：非流式 /api/v1/* JSON → {code, success, data, msg}（BaseResult）
# 需在 CORS 之后注册（更外层先收到 CORS 处理后的响应，封装时保留其头）。
from api.envelope import ApiEnvelopeMiddleware

app.add_middleware(ApiEnvelopeMiddleware)

# 注册路由
app.include_router(health.router)
app.include_router(recommend.router)
app.include_router(chat.router)
app.include_router(chat_images.router)
app.include_router(documents.router)
app.include_router(report.router)
app.include_router(images.router)
app.include_router(evaluation.router)
app.include_router(auth.router)
app.include_router(metrics.router)


def _assert_startup_config() -> None:
    required = {
        "LLM_API_KEY": settings.llm_api_key,
        "LLM_BASE_URL": settings.llm_base_url,
        "LLM_MODEL": settings.llm_model,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise RuntimeError(f"Missing required LLM env vars: {', '.join(missing)}")

    provider = settings.embedding_provider.strip().lower()
    if provider not in ("local", "openai", "dashscope_multimodal"):
        raise RuntimeError(
            f"Unsupported EMBEDDING_PROVIDER: {settings.embedding_provider!r} "
            "(expected local/openai/dashscope_multimodal)"
        )
    if provider != "local":
        missing_emb = [
            name
            for name, value in {
                "EMBEDDING_API_KEY": settings.embedding_api_key,
                "EMBEDDING_MODEL": settings.embedding_model,
            }.items()
            if not str(value).strip()
        ]
        if missing_emb:
            raise RuntimeError(
                f"Missing required embedding env vars for provider {provider!r}: "
                + ", ".join(missing_emb)
            )


if __name__ == "__main__":
    uvicorn.run("agent.app:app", host="0.0.0.0", port=8000, reload=True)
