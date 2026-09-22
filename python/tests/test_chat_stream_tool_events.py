# -*- coding: utf-8 -*-
"""chat/stream 工具终态集成测试：工具失败不升级为顶层 SSE error。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage


def _parse_sse(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            current_event = line[7:]
        elif line.startswith("data: ") and current_event:
            events.append((current_event, json.loads(line[6:])))
            current_event = ""
    return events


@pytest.mark.api
def test_chat_stream_preserves_partial_tool_success():
    from agent.app import app

    async def _astream_events(*args, **kwargs):
        tool_call_message = AIMessage(
            content="",
            tool_calls=[
                {"name": "web_search", "args": {"query": "深圳天气"}, "id": "call-weather", "type": "tool_call"},
                {"name": "query_transcript", "args": {"query": "我的成绩"}, "id": "call-grade", "type": "tool_call"},
            ],
        )
        yield {
            "event": "on_chain_start",
            "name": "tools",
            "run_id": "tools-run",
            "data": {"input": {"messages": [tool_call_message]}},
        }
        yield {
            "event": "on_chain_end",
            "name": "tools",
            "run_id": "tools-run",
            "data": {
                "output": {
                    "messages": [
                        ToolMessage(
                            content="深圳今天晴",
                            tool_call_id="call-weather",
                            name="web_search",
                            status="success",
                        ),
                        ToolMessage(
                            content=json.dumps(
                                {
                                    "isError": True,
                                    "code": "TOOL_TIMEOUT",
                                    "message": "成绩查询超时",
                                    "retryable": True,
                                }
                            ),
                            tool_call_id="call-grade",
                            name="query_transcript",
                            status="error",
                        ),
                    ]
                }
            },
        }
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": MagicMock(content="天气已查到；成绩查询暂不可用。")},
        }

    agent = MagicMock()
    agent.astream_events = _astream_events

    with patch("agent.runtime.main_agent", agent), patch("agent.runtime.chat_session_repo", None):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "查天气和成绩", "session_id": "s-tools", "user_id": ""},
        ) as response:
            body = response.read().decode("utf-8")

    events = _parse_sse(body)
    top_level_errors = [data for event, data in events if event == "error"]
    tool_ends = [
        data
        for event, data in events
        if event == "tool" and data.get("status") == "end"
    ]
    done = [data for event, data in events if event == "done"]

    assert top_level_errors == []
    assert {item["tool_call_id"]: item["ok"] for item in tool_ends} == {
        "call-weather": True,
        "call-grade": False,
    }
    assert next(item for item in tool_ends if item["tool_call_id"] == "call-grade")["code"] == "TOOL_TIMEOUT"
    assert done[0]["reply"] == "天气已查到；成绩查询暂不可用。"


@pytest.mark.api
def test_chat_stream_normalizes_none_error_code():
    from agent.app import app

    error = RuntimeError("上游失败")
    error.code = None

    async def _astream_events(*args, **kwargs):
        if False:
            yield {}
        raise error

    agent = MagicMock()
    agent.astream_events = _astream_events

    with patch("agent.runtime.main_agent", agent), patch("agent.runtime.chat_session_repo", None):
        client = TestClient(app)
        with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "测试错误码", "session_id": "s-none-code", "user_id": ""},
        ) as response:
            body = response.read().decode("utf-8")

    events = _parse_sse(body)
    error_payload = next(data for event, data in events if event == "error")
    assert error_payload["code"] == "RUNTIMEERROR"
    assert error_payload["code"] != "None"
