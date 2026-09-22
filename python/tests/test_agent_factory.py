# -*- coding: utf-8 -*-
"""统一 deepagent 工厂的架构契约测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def factory_settings():
    settings = MagicMock()
    settings.agent_compaction_trigger_messages = 8
    settings.agent_context_window_tokens = 128000
    settings.agent_compaction_keep_tokens = 20000
    with patch("agent.main.factory.get_settings", return_value=settings):
        yield settings


@pytest.mark.asyncio
async def test_shared_factory_loads_scenario_context(factory_settings):
    from agent.main.factory import build_deep_agent
    from agent.main.specs import MAIN_AGENT_SPEC, REPORT_AGENT_SPEC

    backend = MagicMock()
    checkpointer = MagicMock()
    llm = MagicMock()
    compiled_agent = MagicMock()
    tools = [MagicMock(name="inspect_score_excels")]

    with (
        patch("agent.main.factory.build_agent_backend", return_value=backend),
        patch("agent.main.factory.build_checkpointer", new_callable=AsyncMock, return_value=checkpointer) as build_cp,
        patch("agent.main.factory.build_chat_openai", return_value=llm) as build_llm,
        patch("agent.main.factory.create_deep_agent", return_value=compiled_agent) as create_agent,
    ):
        result = await build_deep_agent(REPORT_AGENT_SPEC, tools=tools)

    assert result is compiled_agent.with_config.return_value
    kwargs = create_agent.call_args.kwargs
    assert kwargs["backend"] is backend
    # 无状态场景（use_checkpointer=False）：不建 checkpointer（试金石 10）
    assert kwargs["checkpointer"] is None
    build_cp.assert_not_called()
    assert kwargs["tools"] == tools
    assert kwargs["skills"] == ["/skills/report-generation/"]
    assert kwargs["memory"] == []
    assert kwargs["system_prompt"] == REPORT_AGENT_SPEC.system_prompt
    assert build_llm.call_args.kwargs["task_name"] == REPORT_AGENT_SPEC.task_name
    assert compiled_agent.with_config.call_args.kwargs["run_name"] == REPORT_AGENT_SPEC.task_name.value


@pytest.mark.asyncio
async def test_stateless_spec_skips_checkpointer(factory_settings):
    """无状态三 spec（report/evaluation/recommend）不建 checkpointer；main 保持 True。"""
    from agent.main.factory import build_deep_agent
    from agent.main.specs import (
        EVALUATION_AGENT_SPEC,
        MAIN_AGENT_SPEC,
        RECOMMENDATION_AGENT_SPEC,
        REPORT_AGENT_SPEC,
    )

    assert REPORT_AGENT_SPEC.use_checkpointer is False
    assert EVALUATION_AGENT_SPEC.use_checkpointer is False
    assert RECOMMENDATION_AGENT_SPEC.use_checkpointer is False
    assert MAIN_AGENT_SPEC.use_checkpointer is True  # chat 恢复是既有功能，不得关闭

    backend = MagicMock()
    llm = MagicMock()
    compiled_agent = MagicMock()
    with (
        patch("agent.main.factory.build_agent_backend", return_value=backend),
        patch("agent.main.factory.build_checkpointer", new_callable=AsyncMock) as build_cp,
        patch("agent.main.factory.build_chat_openai", return_value=llm),
        patch("agent.main.factory.create_deep_agent", return_value=compiled_agent) as create_agent,
    ):
        for spec in (REPORT_AGENT_SPEC, EVALUATION_AGENT_SPEC, RECOMMENDATION_AGENT_SPEC):
            await build_deep_agent(spec, tools=[])
    assert build_cp.await_count == 0
    for call in create_agent.call_args_list:
        assert call.kwargs["checkpointer"] is None


@pytest.mark.asyncio
async def test_main_agent_is_a_thin_scenario_entrypoint():
    from agent.main.agent import build_main_agent
    from agent.main.specs import MAIN_AGENT_SPEC

    compiled_agent = MagicMock()
    tools = [MagicMock()]
    with patch("agent.main.agent.build_deep_agent", new_callable=AsyncMock, return_value=compiled_agent) as build:
        result = await build_main_agent(tools=tools)

    assert result is compiled_agent
    build.assert_awaited_once_with(MAIN_AGENT_SPEC, tools=tools, subagents=None)


@pytest.mark.asyncio
async def test_factory_passes_streaming_flag_to_llm(factory_settings):
    """chat/stream 依赖 LLM 的 streaming=True 透出 on_chat_model_stream token 事件。

    回归：此前主 agent LLM 用默认 streaming=False，deepagents 按 stream_mode=updates
    聚合后 chat/stream 永远只能产出空的 done 事件（空回复）。
    """
    from agent.main.factory import build_deep_agent
    from agent.main.specs import MAIN_AGENT_SPEC

    backend = MagicMock()
    checkpointer = MagicMock()
    llm = MagicMock()
    compiled_agent = MagicMock()
    tools = [MagicMock()]

    with (
        patch("agent.main.factory.build_agent_backend", return_value=backend),
        patch("agent.main.factory.build_checkpointer", new_callable=AsyncMock, return_value=checkpointer),
        patch("agent.main.factory.build_chat_openai", return_value=llm) as build_llm,
        patch("agent.main.factory.create_deep_agent", return_value=compiled_agent),
    ):
        await build_deep_agent(MAIN_AGENT_SPEC, tools=tools)

    assert build_llm.call_args.kwargs["streaming"] is True
    assert MAIN_AGENT_SPEC.streaming is True


@pytest.mark.asyncio
async def test_main_agent_passes_compiled_subagents_to_factory(factory_settings):
    """方向 A：main_agent 把预编译业务子 agent（CompiledSubAgent）透传给 deepagent 工厂。"""
    from agent.main.agent import build_main_agent
    from agent.main.specs import MAIN_AGENT_SPEC

    compiled_agent = MagicMock()
    subagents = [{"name": "report_agent", "description": "desc", "runnable": MagicMock()}]
    with patch("agent.main.agent.build_deep_agent", new_callable=AsyncMock, return_value=compiled_agent) as build:
        result = await build_main_agent(subagents=subagents)

    assert result is compiled_agent
    build.assert_awaited_once_with(MAIN_AGENT_SPEC, tools=None, subagents=subagents)


@pytest.mark.asyncio
async def test_factory_forwards_subagents_to_create_deep_agent(factory_settings):
    """create_deep_agent 收到 subagents 参数（deepagents 据此给主 agent 注入 task 工具）。"""
    from agent.main.factory import build_deep_agent
    from agent.main.specs import MAIN_AGENT_SPEC

    backend = MagicMock()
    checkpointer = MagicMock()
    llm = MagicMock()
    compiled_agent = MagicMock()
    subagents = [{"name": "evaluation_agent", "description": "desc", "runnable": MagicMock()}]
    with (
        patch("agent.main.factory.build_agent_backend", return_value=backend),
        patch("agent.main.factory.build_checkpointer", new_callable=AsyncMock, return_value=checkpointer),
        patch("agent.main.factory.build_chat_openai", return_value=llm),
        patch("agent.main.factory.create_deep_agent", return_value=compiled_agent) as create_agent,
    ):
        await build_deep_agent(MAIN_AGENT_SPEC, tools=[], subagents=subagents)

    assert create_agent.call_args.kwargs["subagents"] is subagents


@pytest.mark.asyncio
async def test_business_subagents_are_mounted_instances():
    """4 个业务子 agent 均编译为 CompiledSubAgent：name/description(非空)/runnable 齐全，skill 非空心化。"""
    from agent.main import subagents as sub

    specs = [
        sub.RECOMMENDATION_AGENT_SPEC,
        sub.REPORT_AGENT_SPEC,
        sub.EVALUATION_AGENT_SPEC,
        sub.PPT_AGENT_SPEC,
    ]
    fake = AsyncMock(return_value=MagicMock())
    with (
        patch.object(sub, "build_recommendation_agent", fake),
        patch.object(sub, "build_report_agent", fake),
        patch.object(sub, "build_evaluation_agent", fake),
        patch.object(sub, "build_ppt_agent", fake),
    ):
        compiled = await sub.build_business_subagents()

    assert len(compiled) == 4
    for entry, spec in zip(compiled, specs):
        assert entry["name"] == spec.name
        assert entry["description"].strip(), f"{spec.name} description empty"
        assert entry["runnable"] is fake.return_value
        assert spec.skills, f"{spec.name} skills empty（skill 必须实例挂载）"
        assert spec.allowed_tools, f"{spec.name} allowed_tools empty"

@pytest.mark.asyncio
async def test_factory_mounts_tool_hooks_with_summarization(factory_settings):
    """A0 回归：后续构造 summarization 时不得覆盖已挂载的 ToolHooksMiddleware。"""
    from agent.main.factory import build_deep_agent
    from agent.main.specs import MAIN_AGENT_SPEC
    from agent.middleware.tool_hooks import ToolHooksMiddleware
    from agent.memory.summarization_sync import SummarizationSyncMiddleware

    compiled_agent = MagicMock()
    with (
        patch("agent.main.factory.build_agent_backend", return_value=MagicMock()),
        patch("agent.main.factory.build_checkpointer", new_callable=AsyncMock, return_value=MagicMock()),
        patch("agent.main.factory.build_chat_openai", return_value=MagicMock()),
        patch("agent.main.factory.create_deep_agent", return_value=compiled_agent) as create_agent,
    ):
        await build_deep_agent(MAIN_AGENT_SPEC, tools=[])

    middleware = create_agent.call_args.kwargs["middleware"]
    tool_hooks = [m for m in middleware if isinstance(m, ToolHooksMiddleware)]
    summarization = [m for m in middleware if isinstance(m, SummarizationSyncMiddleware)]
    assert len(tool_hooks) == 1
    assert len(summarization) == 1
