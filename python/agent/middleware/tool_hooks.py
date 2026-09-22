# -*- coding: utf-8 -*-
"""工具横切钩子 middleware（Phase 4 D1/D2/D4 + Chat 韧性修复）。

职责：
- 熔断与同工具连续失败上限；
- 单工具超时/重试与结构化错误 ToolMessage；
- 熔断器/指标/审计记账。

关键约束：预期运行时异常转换为 ToolMessage，使 LangGraph 回到模型节点继续执行；
``asyncio.CancelledError`` 和 LangGraph ``GraphBubbleUp`` 控制流异常必须原样传播。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

if TYPE_CHECKING:
    from tools.errors import ToolExecutionPolicy, ToolInvocationError

logger = logging.getLogger(__name__)


class ToolHooksMiddleware(AgentMiddleware):
    """工具横切钩子：熔断 / 失败上限 / 超时 / 重试 / 记账。"""

    def __init__(
        self,
        *,
        registry: Any = None,
        metrics_collector: Any = None,
        failure_threshold: int = 3,
        policy: ToolExecutionPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._metrics = metrics_collector
        self._failure_threshold = int(failure_threshold)
        if policy is None:
            from tools.errors import DEFAULT_TOOL_EXECUTION_POLICY

            policy = DEFAULT_TOOL_EXECUTION_POLICY
        self._policy = policy
        self._consecutive_failures: dict[str, int] = {}

    # ── before / after ───────────────────────────────────────────────
    def _check_block(self, tool_name: str) -> str | None:
        """返回 block reason；可调用 → None。"""
        if self._consecutive_failures.get(tool_name, 0) >= self._failure_threshold:
            return f"too_many_failures(count={self._consecutive_failures[tool_name]})"
        if self._registry is not None:
            breaker = self._registry._breakers.get(tool_name)
            if breaker is not None and not breaker.can_proceed():
                return f"circuit_open(state={breaker.state})"
        return None

    def _after(
        self,
        tool_name: str,
        ok: bool,
        latency_ms: float,
        error: str = "",
        *,
        breaker_failure: bool = True,
    ) -> None:
        if self._registry is not None:
            breaker = self._registry._breakers.get(tool_name)
            if breaker is not None:
                if ok:
                    breaker.record_success()
                elif breaker_failure:
                    breaker.record_failure()

        if ok:
            self._consecutive_failures.pop(tool_name, None)
        elif breaker_failure:
            self._consecutive_failures[tool_name] = self._consecutive_failures.get(tool_name, 0) + 1

        if self._metrics is not None:
            try:
                self._metrics.record_agent_call(tool_name, ok, latency_ms, error)
            except Exception:  # noqa: BLE001
                pass

    def _blocked_message(self, request: ToolCallRequest, reason: str) -> ToolMessage:
        code = "TOOL_CIRCUIT_OPEN" if reason.startswith("circuit_open") else "TOOL_BLOCKED"
        tc = request.tool_call
        payload = {
            "isError": True,
            "code": code,
            "message": "工具暂时不可用，请稍后重试或改用其他方式",
            "retryable": False,
            "tool": _tool_call_name(tc),
            "fallback_used": False,
            "reason": reason,
        }
        return ToolMessage(
            content=json.dumps(payload, ensure_ascii=False),
            tool_call_id=_tool_call_id(tc) or "call_unknown",
            name=_tool_call_name(tc),
            status="error",
        )

    def _error_message(self, request: ToolCallRequest, error: ToolInvocationError) -> ToolMessage:
        tc = request.tool_call
        return ToolMessage(
            content=json.dumps(
                error.to_payload(tool_name=_tool_call_name(tc)),
                ensure_ascii=False,
            ),
            tool_call_id=_tool_call_id(tc) or "call_unknown",
            name=_tool_call_name(tc),
            status="error",
        )

    async def _invoke_async(self, request: ToolCallRequest, handler):
        from tools.errors import ToolInvocationError, classify_exception, should_retry

        tool_name = _tool_call_name(request.tool_call)
        timeout_s = self._policy.timeout_for(tool_name)
        max_retries = self._policy.max_retries_for(tool_name)
        attempts = max_retries + 1
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        last_error: ToolInvocationError | None = None

        for attempt in range(attempts):
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ToolInvocationError(
                    "TOOL_TIMEOUT",
                    "工具执行超时，请稍后重试",
                    retryable=True,
                )
            try:
                return await asyncio.wait_for(handler(request), timeout=remaining)
            except (asyncio.CancelledError, GraphBubbleUp):
                raise
            except Exception as exc:  # noqa: BLE001
                last_error = classify_exception(exc)
                is_last_attempt = attempt + 1 >= attempts
                if is_last_attempt or not should_retry(last_error):
                    raise last_error from exc
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise last_error from exc
                await asyncio.sleep(min(0.1 * (2**attempt), remaining))

        assert last_error is not None
        raise last_error

    # ── 同步（非流式 invoke 兜底） ───────────────────────────────────
    def wrap_tool_call(self, request: ToolCallRequest, handler):
        from tools.errors import classify_exception, counts_as_breaker_failure

        tool_name = _tool_call_name(request.tool_call)
        reason = self._check_block(tool_name)
        if reason:
            return self._blocked_message(request, reason)

        t0 = time.perf_counter()
        try:
            result = handler(request)
        except (asyncio.CancelledError, GraphBubbleUp):
            raise
        except Exception as exc:  # noqa: BLE001
            error = classify_exception(exc)
            self._after(
                tool_name,
                False,
                (time.perf_counter() - t0) * 1000,
                error.public_message,
                breaker_failure=counts_as_breaker_failure(error),
            )
            return self._error_message(request, error)

        payload = _extract_error_payload(result)
        if payload:
            error = _payload_to_error(payload)
            self._after(
                tool_name,
                False,
                (time.perf_counter() - t0) * 1000,
                error.public_message,
                breaker_failure=counts_as_breaker_failure(error),
            )
        else:
            self._after(tool_name, True, (time.perf_counter() - t0) * 1000)
        return result

    # ── 异步（astream/ainvoke 主路径） ───────────────────────────────
    async def awrap_tool_call(self, request: ToolCallRequest, handler):
        from tools.errors import classify_exception, counts_as_breaker_failure

        tool_name = _tool_call_name(request.tool_call)
        reason = self._check_block(tool_name)
        if reason:
            return self._blocked_message(request, reason)

        t0 = time.perf_counter()
        try:
            result = await self._invoke_async(request, handler)
        except (asyncio.CancelledError, GraphBubbleUp):
            raise
        except Exception as exc:  # noqa: BLE001
            error = classify_exception(exc)
            self._after(
                tool_name,
                False,
                (time.perf_counter() - t0) * 1000,
                error.public_message,
                breaker_failure=counts_as_breaker_failure(error),
            )
            return self._error_message(request, error)

        payload = _extract_error_payload(result)
        if payload:
            error = _payload_to_error(payload)
            self._after(
                tool_name,
                False,
                (time.perf_counter() - t0) * 1000,
                error.public_message,
                breaker_failure=counts_as_breaker_failure(error),
            )
        else:
            self._after(tool_name, True, (time.perf_counter() - t0) * 1000)
        return result


def _tool_call_name(tc) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name", "") or "")
    return str(getattr(tc, "name", "") or "")


def _tool_call_id(tc) -> str:
    if isinstance(tc, dict):
        return str(tc.get("id", "") or "")
    return str(getattr(tc, "id", "") or "")


def _extract_error_payload(result) -> dict[str, Any] | None:
    """从 ToolMessage/dict/JSON 字符串提取 isError 或 error 结构。"""
    content: Any = result.content if isinstance(result, ToolMessage) else result
    payload: Any = content
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped.startswith("{"):
            return None
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("isError") or payload.get("error"):
        return payload
    return None


def _payload_to_error(payload: dict[str, Any]) -> ToolInvocationError:
    from tools.errors import TOOL_BLOCKED, TOOL_INTERNAL, ToolInvocationError

    code = str(payload.get("code", "") or TOOL_INTERNAL)
    message = str(payload.get("message") or payload.get("error") or "工具执行失败")
    if code == TOOL_BLOCKED or str(payload.get("reason", "")).startswith("too_many_failures"):
        code = TOOL_BLOCKED
    return ToolInvocationError(
        code=code,
        public_message=message,
        retryable=bool(payload.get("retryable", False)),
    )


def _is_error_result(result) -> bool:
    return _extract_error_payload(result) is not None


def _result_error(result) -> str:
    payload = _extract_error_payload(result)
    if not payload:
        return ""
    return str(payload.get("message") or payload.get("error") or "")[:120]
