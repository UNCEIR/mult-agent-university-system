# -*- coding: utf-8 -*-
"""ChatToolEventTracker 单元测试：SSE 工具事件以 tools 节点结果为权威。"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from services.chat_tool_events import ChatToolEventTracker


def _start_event(*tool_calls: dict) -> dict:
    message = AIMessage(content="", tool_calls=list(tool_calls))
    return {
        "event": "on_chain_start",
        "name": "tools",
        "run_id": "tools-run-1",
        "data": {"input": {"messages": [message]}},
    }


def _end_event(*messages: ToolMessage) -> dict:
    return {
        "event": "on_chain_end",
        "name": "tools",
        "run_id": "tools-run-1",
        "data": {"output": {"messages": list(messages)}},
    }


@pytest.mark.unit
def test_node_start_emits_start_for_each_call_and_redacts_args():
    tracker = ChatToolEventTracker("s1")
    payloads = tracker.handle(
        _start_event(
            {"name": "web_search", "args": {"query": "深圳天气", "max_results": 5}, "id": "call-w"},
            {"name": "query_transcript", "args": {"query": "我的成绩", "top_k": 3}, "id": "call-t"},
        )
    )

    assert [p["tool_call_id"] for p in payloads] == ["call-w", "call-t"]
    assert all(p["status"] == "start" for p in payloads)
    assert payloads[0]["args"] == {"max_results": 5, "query_chars": 4}
    assert "深圳天气" not in json.dumps(payloads, ensure_ascii=False)


@pytest.mark.unit
def test_node_end_emits_success_and_error_end_by_tool_call_id():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(
        _start_event(
            {"name": "web_search", "args": {"query": "深圳天气"}, "id": "call-w"},
            {"name": "query_transcript", "args": {"query": "成绩"}, "id": "call-t"},
        )
    )
    payloads = tracker.handle(
        _end_event(
            ToolMessage(content="深圳今天晴", tool_call_id="call-w", name="web_search", status="success"),
            ToolMessage(
                content=json.dumps(
                    {"isError": True, "code": "TOOL_TIMEOUT", "message": "查询超时", "retryable": True}
                ),
                tool_call_id="call-t",
                name="query_transcript",
                status="error",
            ),
        )
    )

    by_id = {p["tool_call_id"]: p for p in payloads}
    assert by_id["call-w"]["ok"] is True
    assert "result" not in by_id["call-w"]
    assert by_id["call-t"]["ok"] is False
    assert by_id["call-t"]["code"] == "TOOL_TIMEOUT"
    assert by_id["call-t"]["retryable"] is True


@pytest.mark.unit
def test_blocked_call_without_on_tool_events_still_gets_terminal_event():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(
        _start_event({"name": "web_search", "args": {"query": "天气"}, "id": "call-w"})
    )
    payloads = tracker.handle(
        _end_event(
            ToolMessage(
                content=json.dumps(
                    {"isError": True, "code": "TOOL_BLOCKED", "message": "工具被拦截", "retryable": False}
                ),
                tool_call_id="call-w",
                name="web_search",
                status="error",
            )
        )
    )
    assert payloads[0]["status"] == "end"
    assert payloads[0]["ok"] is False
    assert payloads[0]["code"] == "TOOL_BLOCKED"


@pytest.mark.unit
def test_duplicate_node_end_does_not_duplicate_terminal_event():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(_start_event({"name": "t", "args": {}, "id": "call-1"}))
    end = _end_event(ToolMessage(content="ok", tool_call_id="call-1", name="t", status="success"))
    first = tracker.handle(end)
    second = tracker.handle(end)
    assert len(first) == 1
    assert second == []


@pytest.mark.unit
def test_tool_start_before_canonical_node_start_is_reconciled():
    """真实事件顺序：on_tool_start 可能早于 tools 节点 start，不能生成第二条工具状态。"""
    tracker = ChatToolEventTracker("s1")
    early = tracker.handle(
        {
            "event": "on_tool_start",
            "name": "image_recognize",
            "run_id": "tool-run-1",
            "data": {"input": {"image_ids": ["img_1"]}},
        }
    )
    assert early == []

    starts = tracker.handle(
        _start_event(
            {
                "name": "image_recognize",
                "args": {"image_ids": ["img_1"], "mode": "auto"},
                "id": "call-image-1",
            }
        )
    )
    assert len(starts) == 1
    assert starts[0]["tool_call_id"] == "call-image-1"

    ends = tracker.handle(
        _end_event(
            ToolMessage(
                content='{"kind":"describe","summary":"一张自拍照"}',
                tool_call_id="call-image-1",
                name="image_recognize",
                status="success",
            )
        )
    )
    assert len(ends) == 1
    assert ends[0]["tool_call_id"] == "call-image-1"
    assert ends[0]["ok"] is True
    assert all(item.get("code") != "TOOL_UNFINISHED" for item in ends)


@pytest.mark.unit
def test_node_error_fails_pending_calls():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(_start_event({"name": "t", "args": {}, "id": "call-1"}))
    payloads = tracker.handle(
        {"event": "on_chain_error", "name": "tools", "run_id": "tools-run-1", "data": {}}
    )
    assert payloads[0]["status"] == "end"
    assert payloads[0]["ok"] is False
    assert payloads[0]["code"] == "TOOL_NODE_ERROR"


@pytest.mark.unit
def test_on_tool_error_is_used_if_node_end_has_no_message():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(_start_event({"name": "t", "args": {}, "id": "call-1"}))
    tracker.handle(
        {
            "event": "on_tool_start",
            "name": "t",
            "run_id": "tool-run-1",
            "data": {"input": {}},
        }
    )
    tracker.handle(
        {
            "event": "on_tool_error",
            "name": "t",
            "run_id": "tool-run-1",
            "data": {"error": RuntimeError("boom")},
        }
    )
    payloads = tracker.handle({"event": "on_chain_end", "name": "tools", "data": {"output": {"messages": []}}})
    assert payloads[0]["ok"] is False
    assert payloads[0]["tool_call_id"] == "call-1"


@pytest.mark.unit
def test_fail_pending_for_stream_end():
    tracker = ChatToolEventTracker("s1")
    tracker.handle(_start_event({"name": "t", "args": {}, "id": "call-1"}))
    payloads = tracker.fail_pending("TOOL_UNFINISHED", "工具未返回最终结果")
    assert payloads[0]["status"] == "end"
    assert payloads[0]["ok"] is False
    assert payloads[0]["code"] == "TOOL_UNFINISHED"
