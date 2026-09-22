# -*- coding: utf-8 -*-
"""工具调用统一错误契约与执行策略。

工具异常在 middleware / 编排器边界转换为 ``ToolInvocationError``，再序列化为
``ToolMessage(status="error")``。这样 LangGraph 可以把失败结果交回模型，模型仍可
继续调用独立的兄弟工具；只有控制流异常继续向上传播。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Mapping

import httpx
from pydantic import ValidationError

TOOL_TIMEOUT = "TOOL_TIMEOUT"
TOOL_CONNECTION = "TOOL_CONNECTION"
TOOL_UPSTREAM_5XX = "TOOL_UPSTREAM_5XX"
TOOL_CIRCUIT_OPEN = "TOOL_CIRCUIT_OPEN"
TOOL_BLOCKED = "TOOL_BLOCKED"
AUTH_REQUIRED = "AUTH_REQUIRED"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
NO_DATA = "NO_DATA"
TOOL_INTERNAL = "TOOL_INTERNAL"
VISION_TIMEOUT = "VISION_TIMEOUT"
VISION_RATE_LIMITED = "VISION_RATE_LIMITED"
VISION_UPSTREAM_5XX = "VISION_UPSTREAM_5XX"
IMAGE_FETCH_TIMEOUT = "IMAGE_FETCH_TIMEOUT"

RETRYABLE_CODES = frozenset({TOOL_TIMEOUT, TOOL_CONNECTION, TOOL_UPSTREAM_5XX, VISION_TIMEOUT, VISION_RATE_LIMITED, VISION_UPSTREAM_5XX, IMAGE_FETCH_TIMEOUT})
BREAKER_FAILURE_CODES = frozenset({TOOL_TIMEOUT, TOOL_CONNECTION, TOOL_UPSTREAM_5XX, TOOL_INTERNAL, VISION_TIMEOUT, VISION_RATE_LIMITED, VISION_UPSTREAM_5XX, IMAGE_FETCH_TIMEOUT})
NON_FAILURE_CODES = frozenset(
    {NO_DATA, AUTH_REQUIRED, INVALID_ARGUMENT, TOOL_BLOCKED, TOOL_CIRCUIT_OPEN}
)


@dataclass
class ToolInvocationError(Exception):
    """稳定的工具错误，不携带堆栈或敏感细节。"""

    code: str
    public_message: str
    retryable: bool = False
    cause: Exception | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        super().__init__(self.public_message)

    def to_payload(self, *, tool_name: str = "", fallback_used: bool = False) -> dict:
        return {
            "isError": True,
            "code": self.code,
            "message": self.public_message,
            "retryable": self.retryable,
            "tool": tool_name,
            "fallback_used": fallback_used,
        }


@dataclass(frozen=True)
class ToolExecutionPolicy:
    """每工具超时与重试策略，主调用和 fallback 共享同一份配置。"""

    default_timeout_s: float = 30.0
    default_max_retries: int = 0
    tool_timeouts: Mapping[str, float] = field(default_factory=dict)
    tool_max_retries: Mapping[str, int] = field(default_factory=dict)

    def timeout_for(self, tool_name: str) -> float:
        value = float(self.tool_timeouts.get(tool_name, self.default_timeout_s))
        return max(0.01, value)

    def max_retries_for(self, tool_name: str) -> int:
        return max(0, int(self.tool_max_retries.get(tool_name, self.default_max_retries)))


DEFAULT_TOOL_EXECUTION_POLICY = ToolExecutionPolicy(
    default_timeout_s=30.0,
    default_max_retries=0,
    tool_timeouts={
        "web_search": 20.0,
        "query_handbook": 15.0,
        "query_transcript": 15.0,
        "image_recognize": 75.0,
        "get_current_time": 2.0,
        "parse_document": 30.0,
        "chunk_document": 20.0,
    },
    tool_max_retries={
        "web_search": 1,
        "query_handbook": 1,
        "query_transcript": 1,
        "image_recognize": 1,
    },
)


def should_retry(error: ToolInvocationError) -> bool:
    return error.retryable or error.code in RETRYABLE_CODES


def counts_as_breaker_failure(error: ToolInvocationError) -> bool:
    return error.code in BREAKER_FAILURE_CODES


def classify_exception(exc: Exception) -> ToolInvocationError:
    """把底层异常转换为稳定错误码；不返回原始异常文本给用户。"""
    if isinstance(exc, ToolInvocationError):
        return exc

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ToolInvocationError(
            TOOL_TIMEOUT,
            "工具执行超时，请稍后重试",
            retryable=True,
            cause=exc,
        )

    if isinstance(exc, (httpx.TimeoutException,)):
        return ToolInvocationError(
            TOOL_TIMEOUT,
            "外部服务响应超时，请稍后重试",
            retryable=True,
            cause=exc,
        )

    if isinstance(exc, (httpx.ConnectError, httpx.NetworkError, ConnectionError)):
        return ToolInvocationError(
            TOOL_CONNECTION,
            "外部服务暂时不可用，请稍后重试",
            retryable=True,
            cause=exc,
        )

    if isinstance(exc, PermissionError):
        return ToolInvocationError(
            AUTH_REQUIRED,
            "当前身份无权执行该操作",
            retryable=False,
            cause=exc,
        )

    if isinstance(exc, (ValueError, TypeError, ValidationError)):
        return ToolInvocationError(
            INVALID_ARGUMENT,
            "工具参数不合法",
            retryable=False,
            cause=exc,
        )

    raw_code = str(getattr(exc, "code", "") or "").strip().upper()
    if raw_code in NON_FAILURE_CODES or raw_code in BREAKER_FAILURE_CODES:
        return ToolInvocationError(
            raw_code,
            _message_for_code(raw_code),
            retryable=raw_code in RETRYABLE_CODES,
            cause=exc,
        )

    return ToolInvocationError(
        TOOL_INTERNAL,
        "工具执行失败，请稍后重试",
        retryable=False,
        cause=exc,
    )


def _message_for_code(code: str) -> str:
    return {
        TOOL_TIMEOUT: "工具执行超时，请稍后重试",
        TOOL_CONNECTION: "外部服务暂时不可用，请稍后重试",
        TOOL_UPSTREAM_5XX: "上游服务暂时异常，请稍后重试",
        TOOL_CIRCUIT_OPEN: "工具暂时熔断，请稍后重试",
        TOOL_BLOCKED: "工具调用已被策略拦截",
        AUTH_REQUIRED: "当前身份无权执行该操作",
        INVALID_ARGUMENT: "工具参数不合法",
        NO_DATA: "未查询到符合条件的数据",
        TOOL_INTERNAL: "工具执行失败，请稍后重试",
    }.get(code, "工具执行失败，请稍后重试")


__all__ = [
    "AUTH_REQUIRED",
    "BREAKER_FAILURE_CODES",
    "DEFAULT_TOOL_EXECUTION_POLICY",
    "INVALID_ARGUMENT",
    "NO_DATA",
    "RETRYABLE_CODES",
    "TOOL_BLOCKED",
    "TOOL_CIRCUIT_OPEN",
    "TOOL_CONNECTION",
    "TOOL_INTERNAL",
    "TOOL_TIMEOUT",
    "TOOL_UPSTREAM_5XX",
    "ToolExecutionPolicy",
    "ToolInvocationError",
    "classify_exception",
    "counts_as_breaker_failure",
    "should_retry",
]
