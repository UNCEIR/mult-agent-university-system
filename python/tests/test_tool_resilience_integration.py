# -*- coding: utf-8 -*-
"""真实 ToolNode 集成测试：工具失败不会阻断模型继续执行和最终合成。"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from agent.middleware.tool_hooks import ToolHooksMiddleware


class _ToolCallingFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.integration
@pytest.mark.asyncio
async def test_toolnode_error_toolmessage_allows_next_model_turn():
    calls: list[str] = []

    @tool
    def failing_tool(query: str) -> str:
        """Always raises for resilience testing."""
        calls.append("failing")
        raise RuntimeError("boom")

    @tool
    def healthy_tool(query: str) -> str:
        """Return a healthy result."""
        calls.append("healthy")
        return json.dumps({"result": "ok"})

    model = _ToolCallingFakeModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "failing_tool",
                            "args": {"query": "a"},
                            "id": "call-fail",
                            "type": "tool_call",
                        },
                        {
                            "name": "healthy_tool",
                            "args": {"query": "b"},
                            "id": "call-ok",
                            "type": "tool_call",
                        },
                    ],
                ),
                AIMessage(content="已合并部分成功结果"),
            ]
        )
    )
    agent = create_agent(
        model=model,
        tools=[failing_tool, healthy_tool],
        middleware=[ToolHooksMiddleware(registry=None)],
    )

    result = await asyncio.wait_for(
        agent.ainvoke({"messages": [{"role": "user", "content": "执行两个工具"}]}),
        timeout=5,
    )
    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    by_id = {m.tool_call_id: m for m in tool_messages}

    assert calls == ["failing", "healthy"] or calls == ["healthy", "failing"]
    assert by_id["call-fail"].status == "error"
    assert json.loads(by_id["call-fail"].content)["code"] == "TOOL_INTERNAL"
    assert by_id["call-ok"].status == "success"
    assert result["messages"][-1].content == "已合并部分成功结果"
