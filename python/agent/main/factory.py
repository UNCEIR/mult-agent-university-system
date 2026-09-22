# -*- coding: utf-8 -*-
"""统一 deepagent 工厂。

业务模块只声明 AgentSpec，不重复组装 deepagents middleware、backend 和
checkpointer。这样每个场景可以拥有自己的 skills、memory 和 tool allowlist。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from deepagents import create_deep_agent
from deepagents.middleware.summarization import (
    SummarizationMiddleware,
    SummarizationToolMiddleware,
)

from ai.llm_client import build_chat_openai
from config import get_settings

from agent.memory.summarization_sync import SummarizationSyncMiddleware
from agent.middleware.tool_hooks import ToolHooksMiddleware
from .backend import build_agent_backend
from .checkpointer import build_checkpointer
from .specs import AgentSpec

logger = structlog.get_logger()

# Phase 4（A5）：双模板 —— 首轮六节 summarize.txt（替代旧五字段 summarization.txt，旧文件废弃）；
# 已有 compaction 用 summarization_update.txt（preserve/add/update/可删规则 + <previous-summary>）。
_SUMMARIZE_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "summarize.txt"
_UPDATE_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "summarization_update.txt"


def _load_summarization_prompt() -> str | None:
    """读取首轮六节压缩模板（Phase 4 A5：读取路径已从旧五字段文件改向 summarize.txt）。"""
    try:
        return _SUMMARIZE_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("build_deep_agent.summarization_prompt_missing", path=str(_SUMMARIZE_PROMPT_PATH))
        return None


def _load_update_prompt() -> str | None:
    """读取增量合并模板（<previous-summary> + preserve/add/update/可删规则）。"""
    try:
        return _UPDATE_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        logger.warning("build_deep_agent.summarization_update_prompt_missing", path=str(_UPDATE_PROMPT_PATH))
        return None


async def build_deep_agent(
    spec: AgentSpec,
    *,
    tools: list[Any] | None = None,
    subagents: list[dict] | None = None,
):
    """按业务规格创建一个 deepagent。

    subagents: 预编译的子 agent（CompiledSubAgent dict 列表），传给 create_deep_agent，
    使主 agent 获得 task() 委派工具（子 agent 各挂自身 skills+allowed_tools）。
    """
    settings = get_settings()
    backend = build_agent_backend()
    checkpointer = await build_checkpointer() if spec.use_checkpointer else None

    # build_chat_openai 通过 pydantic 的 name 字段命名 trace，保持 BaseChatModel
    # 类型，deepagents 可正常解析；LangSmith 中 LLM run 显示 spec.task_name。
    llm = build_chat_openai(
        temperature=spec.temperature,
        max_tokens=spec.max_tokens,
        streaming=spec.streaming,
        task_name=spec.task_name,
    )

    if tools is None:
        from agent import runtime

        registry = runtime.tool_registry
        if registry is None:
            tools = []
        elif spec.allowed_tools:
            tools = registry.get_all(allowed=list(spec.allowed_tools))
        else:
            tools = registry.get_all()

    middleware: list[Any] = []
    # D1/D2/D4：工具横切钩子（熔断/失败上限/记账/审计）——对所有 spec 生效；
    # deepagents ToolNode 直调 StructuredTool.invoke，钩子必须挂 middleware。
    from agent import runtime as _runtime

    _registry = getattr(_runtime, "tool_registry", None)
    _metrics = getattr(_runtime, "metrics_collector", None)
    middleware.append(
        ToolHooksMiddleware(registry=_registry, metrics_collector=_metrics)
    )
    if spec.enable_compaction:
            trigger = (
                ("messages", settings.agent_compaction_trigger_messages)
                if settings.agent_compaction_trigger_messages
                else ("tokens", settings.agent_context_window_tokens - 13000)
            )
    summary_prompt = _load_summarization_prompt()
    update_prompt = _load_update_prompt()
        # Phase 4（A4/A5/A6）：压缩 middleware 子类化 —— 写后同步落 chat_session_compactions、
        # fallback 前缀检测、双模板。repo 无（如 report/evaluation 子 agent）→ middleware no-op。
    _repo = getattr(_runtime, "chat_session_repo", None)
    summarization = SummarizationSyncMiddleware(
            model=llm,
            backend=backend,
            trigger=trigger,
            keep=("tokens", settings.agent_compaction_keep_tokens),
            summary_prompt=summary_prompt,
            repo=_repo,
            summarize_prompt=summary_prompt,
            update_prompt=update_prompt,
        )
        # 第二项必须同步传子类实例（v1.2 修正），否则 nudge 中间件与子类事件脱节
    middleware = [*middleware, summarization, SummarizationToolMiddleware(summarization)]

    logger.info(
        "build_deep_agent",
        agent_name=spec.name,
        task_name=spec.task_name.value,
        tool_count=len(tools),
        skills=list(spec.skills),
        memory=list(spec.memory),
    )

    # Phase 2 多租户修复：main agent 对 AGENTS.md 代码级禁写
    # （deepagents MemoryMiddleware 内置 <memory_guidelines> 会主动教唆写回，
    # 仅靠 prompt 约束不可控；用户级记忆一律走 chat_memory_entries 表）
    permissions = None
    if spec.name == "main_agent":
        from deepagents import FilesystemPermission

        permissions = [
            FilesystemPermission(operations=["write"], paths=["/memories/AGENTS.md"], mode="deny")
        ]

    agent = create_deep_agent(
        model=llm,
        tools=tools,
        backend=backend,
        skills=list(spec.skills),
        memory=list(spec.memory),
        checkpointer=checkpointer,
        system_prompt=spec.system_prompt,
        middleware=middleware,
        permissions=permissions,
        subagents=subagents,
    )

    # 外层 agent run 也以业务名标识（图级别的 trace 根节点），LLM 层的
    # run 名由 build_chat_openai 的 name 字段承载，两层在 LangSmith 中一致。
    if spec.task_name:
        return agent.with_config(run_name=spec.task_name.value)

    return agent
