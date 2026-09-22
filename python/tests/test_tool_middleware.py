# -*- coding: utf-8 -*-
"""工具横切钩子 middleware 单测（D1/D2/D4/D5/D7 + Chat 韧性修复）。"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from agent.middleware.tool_hooks import ToolHooksMiddleware
from ai.llm_client import LLMError
from tools.circuit_breaker import CircuitBreaker
from tools.errors import ToolExecutionPolicy, ToolInvocationError


class _FakeRegistry:
    def __init__(self, breaker: CircuitBreaker):
        self._breakers = {"tool_x": breaker}


class _FakeMetrics:
    def __init__(self):
        self.calls = []

    def record_agent_call(self, agent_name, success, latency_ms, error=""):
        self.calls.append((agent_name, success))


def _request(name: str = "tool_x") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": "call_1", "type": "tool_call"},
        tool=None,
        state=None,
        runtime=None,
    )


def _ok_handler(req):
    return ToolMessage(content="ok", tool_call_id="call_1", name=req.tool_call["name"], status="success")


def _payload(result: ToolMessage) -> dict:
    return json.loads(result.content)


# ── D2 熔断 ────────────────────────────────────────────────────────
@pytest.mark.unit
def test_circuit_open_blocks_before_handler():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    for _ in range(3):
        breaker.record_failure()
    mw = ToolHooksMiddleware(registry=_FakeRegistry(breaker))
    called = {"n": 0}

    def handler(req):
        called["n"] += 1
        return _ok_handler(req)

    result = mw.wrap_tool_call(_request(), handler)
    assert called["n"] == 0
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert _payload(result)["code"] == "TOOL_CIRCUIT_OPEN"
    assert "circuit_open" in _payload(result)["reason"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_awrap_circuit_open_blocks():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    for _ in range(3):
        breaker.record_failure()
    mw = ToolHooksMiddleware(registry=_FakeRegistry(breaker))
    called = {"n": 0}

    async def handler(req):
        called["n"] += 1
        return _ok_handler(req)

    result = await mw.awrap_tool_call(_request(), handler)
    assert called["n"] == 0
    assert result.status == "error"
    assert _payload(result)["code"] == "TOOL_CIRCUIT_OPEN"


# ── 异常转 ToolMessage ────────────────────────────────────────────
@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_exception_becomes_error_tool_message():
    mw = ToolHooksMiddleware(registry=None)

    async def failing_handler(req):
        raise RuntimeError("boom")

    result = await mw.awrap_tool_call(_request(), failing_handler)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert _payload(result)["code"] == "TOOL_INTERNAL"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_async_timeout_becomes_error_tool_message():
    policy = ToolExecutionPolicy(default_timeout_s=0.01, default_max_retries=0)
    mw = ToolHooksMiddleware(registry=None, policy=policy)

    async def slow_handler(req):
        await asyncio.sleep(0.05)
        return _ok_handler(req)

    result = await mw.awrap_tool_call(_request(), slow_handler)
    assert result.status == "error"
    assert _payload(result)["code"] == "TOOL_TIMEOUT"
    assert _payload(result)["retryable"] is True


@pytest.mark.unit
def test_invalid_argument_becomes_error_tool_message():
    mw = ToolHooksMiddleware(registry=None)

    def handler(req):
        raise ValueError("bad input")

    result = mw.wrap_tool_call(_request(), handler)
    assert result.status == "error"
    assert _payload(result)["code"] == "INVALID_ARGUMENT"


@pytest.mark.unit
def test_tool_invocation_error_preserves_code():
    mw = ToolHooksMiddleware(registry=None)

    def handler(req):
        raise ToolInvocationError("AUTH_REQUIRED", "需要登录", retryable=False)

    result = mw.wrap_tool_call(_request(), handler)
    assert result.status == "error"
    assert _payload(result)["code"] == "AUTH_REQUIRED"
    assert _payload(result)["message"] == "需要登录"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_error_is_not_converted():
    mw = ToolHooksMiddleware(registry=None)

    async def cancelled_handler(req):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await mw.awrap_tool_call(_request(), cancelled_handler)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_graph_bubble_up_is_not_converted():
    mw = ToolHooksMiddleware(registry=None)

    async def control_handler(req):
        raise GraphBubbleUp("halt")

    with pytest.raises(GraphBubbleUp):
        await mw.awrap_tool_call(_request(), control_handler)


# ── D4 同工具失败上限 ──────────────────────────────────────────────
@pytest.mark.unit
def test_failure_threshold_blocks_fourth_call():
    mw = ToolHooksMiddleware(registry=None, failure_threshold=3)

    def failing_handler(req):
        raise RuntimeError("boom")

    for _ in range(3):
        result = mw.wrap_tool_call(_request(), failing_handler)
        assert result.status == "error"

    result = mw.wrap_tool_call(_request(), failing_handler)
    assert isinstance(result, ToolMessage)
    assert _payload(result)["code"] == "TOOL_BLOCKED"
    assert "too_many_failures" in _payload(result)["reason"]


@pytest.mark.unit
def test_success_resets_failure_count():
    mw = ToolHooksMiddleware(registry=None, failure_threshold=3)

    def failing_handler(req):
        raise RuntimeError("boom")

    for _ in range(2):
        assert mw.wrap_tool_call(_request(), failing_handler).status == "error"
    mw.wrap_tool_call(_request(), _ok_handler)
    mw.wrap_tool_call(_request(), failing_handler)
    result = mw.wrap_tool_call(_request(), _ok_handler)
    assert not isinstance(result, ToolMessage) or result.status != "error"


# ── D1 记账/埋点 ───────────────────────────────────────────────────
@pytest.mark.unit
def test_after_records_breaker_and_metrics():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    metrics = _FakeMetrics()
    mw = ToolHooksMiddleware(registry=_FakeRegistry(breaker), metrics_collector=metrics)
    mw.wrap_tool_call(_request(), _ok_handler)
    assert metrics.calls == [("tool_x", True)]


@pytest.mark.unit
def test_after_records_breaker_failure_on_error_result():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    mw = ToolHooksMiddleware(registry=_FakeRegistry(breaker))

    def err_handler(req):
        return ToolMessage(
            content=json.dumps({"isError": True, "code": "TOOL_INTERNAL", "message": "bad"}),
            tool_call_id="call_1",
            name="tool_x",
            status="error",
        )

    mw.wrap_tool_call(_request(), err_handler)
    assert breaker._failure_count == 1


@pytest.mark.unit
def test_non_runtime_error_does_not_count_as_breaker_failure():
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
    mw = ToolHooksMiddleware(registry=_FakeRegistry(breaker))

    def auth_handler(req):
        raise PermissionError("login required")

    result = mw.wrap_tool_call(_request(), auth_handler)
    assert result.status == "error"
    assert _payload(result)["code"] == "AUTH_REQUIRED"
    assert breaker._failure_count == 0


# ── D5 意图说明书互相点名 ──────────────────────────────────────────
@pytest.mark.unit
def test_knowledge_tools_description_mutual_naming():
    from tools.knowledge.query_handbook import query_handbook
    from tools.knowledge.query_transcript import query_transcript

    assert "query_transcript" in query_handbook.description
    assert "query_handbook" in query_transcript.description
    assert "何时用" in query_handbook.description
    assert "何时用" in query_transcript.description


# ── D7 类型化错误码 ────────────────────────────────────────────────
@pytest.mark.unit
def test_llm_error_code_attribute():
    exc = LLMError("quota", "配额不足")
    assert exc.code == "quota"
    assert getattr(exc, "code", type(exc).__name__.upper()) == "quota"
