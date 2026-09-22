# -*- coding: utf-8 -*-
"""main agent 插件测试：web_search MCP 路径与降级、image_recognize、writing、mindmap。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.fake_mcp_server import install_fake_tools
from tools.mcp_client import MultiServerMCPClient, reset_mcp_client


@pytest.fixture
def mcp_client():
    reset_mcp_client()
    yield MultiServerMCPClient()
    reset_mcp_client()


@pytest.mark.unit
async def test_web_search_mcp_primary_path(mcp_client):
    """web_search 走 MCP 主路（tavily 服务器）。"""
    install_fake_tools(mcp_client)
    with patch("tools.mcp_client.get_mcp_client", return_value=mcp_client), patch(
        "tools.chat.web_search._tavily_fallback"
    ) as fallback:
        from tools.chat.web_search import web_search

        result = await web_search.ainvoke({"query": "大学生创业政策"})
        assert "大学生创业政策" in result
        fallback.assert_not_called()  # MCP 主路成功不触发兜底


@pytest.mark.unit
async def test_web_search_mcp_fail_falls_back(mcp_client):
    """MCP 失败 → tavily 直连兜底（不静默）。"""
    install_fake_tools(mcp_client, fail_on_call=True)
    with patch("tools.mcp_client.get_mcp_client", return_value=mcp_client), patch(
        "tools.chat.web_search._tavily_fallback", return_value={"query": "q", "results": [], "source": "tavily-direct"}
    ) as fallback:
        from tools.chat.web_search import web_search

        result = await web_search.ainvoke({"query": "q"})
        assert "tavily-direct" in result
        fallback.assert_called_once()


@pytest.mark.unit
async def test_image_generate_mcp_failure_no_fake_image(mcp_client):
    """MCP 失败 → 结构化 error（不伪造图片）。"""
    with patch("tools.mcp_client.get_mcp_client", return_value=mcp_client):
        from tools.image.image_generate import image_generate

        result = await image_generate.ainvoke({"prompt": "一只猫"})
        assert "isError" in result or "MCP" in result


@pytest.mark.unit
async def test_image_recognize_vision(mcp_client):
    """image_recognize 视觉直连（mock vision LLM）。"""
    from tools.image.image_recognize import image_recognize

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="这是一张课程表图片"))
    with patch("tools.image.image_recognize._build_vision_llm", return_value=llm), patch(
        "tools.image.image_recognize._load_image_data_urls", new=AsyncMock(return_value=["data:image/png;base64,AA=="])
    ):
        result = await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345"], "question": "这是什么", "mode": "describe"})
    assert "课程表" in result


@pytest.mark.unit
async def test_writing_assistant_generates():
    from tools.chat.writing_assistant import writing_assistant

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="这是一篇关于深度学习的综述。"))
    with patch("tools.chat.writing_assistant.build_chat_openai", return_value=llm):
        result = await writing_assistant.ainvoke({"topic": "深度学习综述", "genre": "学术论文", "word_count": 500})
    assert "深度学习" in result


@pytest.mark.unit
async def test_mindmap_generator_renders_html():
    from tools.mindmap.mindmap_generator import mindmap_generator

    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="- 主题\n  - 分支一\n  - 分支二"))
    with patch("tools.mindmap.mindmap_generator._generate_outline_llm", return_value=llm):
        result = await mindmap_generator.ainvoke({"topic": "选课规划"})
    assert "mindmap_" in result
    assert ".html" in result


@pytest.mark.unit
def test_chat_request_images_field():
    from api.chat import ChatRequest

    req = ChatRequest(message="看看这张图", images=["data:image/png;base64,AA=="])
    assert req.images == ["data:image/png;base64,AA=="]
    assert len(req.images) <= 4


@pytest.mark.unit
def test_main_agent_spec_removed_compute_weighted_grade():
    """主链不再暴露 stub 工具（评审 P2-10）。"""
    from agent.main.specs import MAIN_AGENT_SPEC

    assert "compute_weighted_grade" not in MAIN_AGENT_SPEC.allowed_tools
    assert "image_recognize" in MAIN_AGENT_SPEC.allowed_tools
