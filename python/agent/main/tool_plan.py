# -*- coding: utf-8 -*-
"""确定性工具计划执行器。

用于需要严格依赖、fallback 或失败隔离的内部 workflow：
- 无依赖步骤按波次并行；
- 依赖步骤串行；
- 前置失败时只跳过依赖分支，独立分支继续；
- 主调用与 fallback 都受显式 allowlist、schema 和总 deadline 约束。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from pydantic import BaseModel, ValidationError

from agent.middleware.tool_hooks import ToolHooksMiddleware
from tools.errors import (
    INVALID_ARGUMENT,
    NO_DATA,
    TOOL_INTERNAL,
    TOOL_TIMEOUT,
    ToolExecutionPolicy,
    ToolInvocationError,
)

StepStatus = Literal["success", "fallback_success", "no_data", "failed", "skipped"]


@dataclass(frozen=True)
class ToolFallback:
    tool_name: str
    build_args: Callable[[dict[str, "ToolStepResult"]], dict[str, Any]]
    timeout_s: float = 20.0
    max_retries: int = 0
    output_schema: type[BaseModel] | None = None


@dataclass(frozen=True)
class ToolStep:
    step_id: str
    tool_name: str
    build_args: Callable[[dict[str, "ToolStepResult"]], dict[str, Any]]
    depends_on: tuple[str, ...] = ()
    required: bool = True
    max_retries: int = 1
    timeout_s: float = 20.0
    output_schema: type[BaseModel] | None = None
    fallback: ToolFallback | None = None


@dataclass(frozen=True)
class ToolPlan:
    plan_id: str
    steps: tuple[ToolStep, ...]
    allowed_tools: frozenset[str]
    deadline_s: float = 60.0


@dataclass
class ToolStepResult:
    step_id: str
    tool_name: str
    status: StepStatus
    data: Any = None
    error: ToolInvocationError | None = None
    attempts: int = 0
    provider: str | None = None
    fallback_used: bool = False
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in {"success", "fallback_success"}

    def summary(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step_id": self.step_id,
            "tool": self.tool_name,
            "status": self.status,
            "fallback_used": self.fallback_used,
            "latency_ms": self.latency_ms,
        }
        if self.error:
            payload["code"] = self.error.code
            payload["message"] = self.error.public_message
        return payload


class ToolPlanExecutor:
    """按 ToolPlan 执行工具 DAG。"""

    def __init__(self, registry: Any, *, metrics_collector: Any = None) -> None:
        self.registry = registry
        self.metrics_collector = metrics_collector

    def validate(self, plan: ToolPlan) -> None:
        if plan.deadline_s <= 0:
            raise ValueError("deadline_s must be positive")
        ids = [step.step_id for step in plan.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step_id")
        known = set(ids)
        for step in plan.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(f"unknown dependencies for {step.step_id}: {sorted(missing)}")
            self._validate_tool(plan, step.tool_name)
            if not callable(step.build_args):
                raise ValueError(f"build_args is not callable for {step.step_id}")
            if step.fallback:
                self._validate_tool(plan, step.fallback.tool_name)
                if not callable(step.fallback.build_args):
                    raise ValueError(f"fallback build_args is not callable for {step.step_id}")
        self._validate_acyclic(plan)

    async def run(
        self,
        plan: ToolPlan,
        outputs: dict[str, ToolStepResult] | None = None,
    ) -> dict[str, ToolStepResult]:
        self.validate(plan)
        results: dict[str, ToolStepResult] = dict(outputs or {})
        pending = {step.step_id: step for step in plan.steps if step.step_id not in results}
        deadline = time.monotonic() + plan.deadline_s

        while pending:
            ready: list[ToolStep] = []
            for step in list(pending.values()):
                statuses = [results[dep].status for dep in step.depends_on if dep in results]
                if len(statuses) != len(step.depends_on):
                    continue
                if any(status in {"failed", "skipped"} for status in statuses):
                    results[step.step_id] = ToolStepResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status="skipped",
                        error=ToolInvocationError(
                            "DEPENDENCY_SKIPPED",
                            "前置步骤未成功，当前步骤未执行",
                        ),
                    )
                    pending.pop(step.step_id, None)
                else:
                    ready.append(step)

            if not ready:
                if pending:
                    raise ValueError("tool plan is not executable; possible cycle")
                break

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for step in ready:
                    results[step.step_id] = ToolStepResult(
                        step_id=step.step_id,
                        tool_name=step.tool_name,
                        status="failed",
                        error=ToolInvocationError(TOOL_TIMEOUT, "工具计划执行超时", retryable=True),
                    )
                    pending.pop(step.step_id, None)
                continue

            wave = [
                asyncio.create_task(self._execute_step(step, results, min(step.timeout_s, remaining)))
                for step in ready
            ]
            try:
                completed = await asyncio.wait_for(asyncio.gather(*wave), timeout=remaining)
            except asyncio.CancelledError:
                raise
            except (TimeoutError, asyncio.TimeoutError):
                completed = [self._timeout_result(step) for step in ready]
            for step, result in zip(ready, completed, strict=True):
                results[step.step_id] = result
                pending.pop(step.step_id, None)

        return results

    async def run_parallel(self, plan: ToolPlan) -> dict[str, ToolStepResult]:
        if any(step.depends_on for step in plan.steps):
            raise ValueError("run_parallel requires dependency-free steps")
        return await self.run(plan)

    def final_context(self, results: dict[str, ToolStepResult]) -> dict[str, Any]:
        values = list(results.values())
        if values and all(result.ok for result in values):
            overall = "success"
        elif any(result.ok for result in values):
            overall = "partial_success"
        else:
            overall = "failed"
        return {"overall_status": overall, "steps": [result.summary() for result in values]}

    def _validate_tool(self, plan: ToolPlan, tool_name: str) -> None:
        if tool_name not in plan.allowed_tools:
            raise ValueError(f"tool not allowed by plan: {tool_name}")
        if self.registry is None or self.registry.get(tool_name) is None:
            raise ValueError(f"tool not registered: {tool_name}")
        is_internal = getattr(self.registry, "is_internal", None)
        if callable(is_internal) and is_internal(tool_name):
            raise ValueError(f"internal tool cannot be invoked by plan: {tool_name}")

    @staticmethod
    def _validate_acyclic(plan: ToolPlan) -> None:
        deps = {step.step_id: set(step.depends_on) for step in plan.steps}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("tool plan contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dep in deps[node]:
                visit(dep)
            visiting.remove(node)
            visited.add(node)

        for node in deps:
            visit(node)

    async def _execute_step(
        self,
        step: ToolStep,
        outputs: dict[str, ToolStepResult],
        remaining_s: float,
    ) -> ToolStepResult:
        started = time.perf_counter()
        try:
            args = step.build_args(outputs)
            primary = await self._invoke(
                tool_name=step.tool_name,
                args=args,
                timeout_s=min(step.timeout_s, remaining_s),
                max_retries=step.max_retries,
                output_schema=step.output_schema,
            )
            if primary.status == "failed" and step.fallback is not None:
                fallback_args = step.fallback.build_args(outputs)
                fallback = await self._invoke(
                    tool_name=step.fallback.tool_name,
                    args=fallback_args,
                    timeout_s=min(step.fallback.timeout_s, remaining_s),
                    max_retries=step.fallback.max_retries,
                    output_schema=step.fallback.output_schema or step.output_schema,
                )
                if fallback.ok or fallback.status == "no_data":
                    fallback.status = "fallback_success" if fallback.ok else "no_data"
                    fallback.fallback_used = True
                    fallback.step_id = step.step_id
                    fallback.latency_ms = round((time.perf_counter() - started) * 1000, 1)
                    return fallback
            primary.step_id = step.step_id
            primary.latency_ms = round((time.perf_counter() - started) * 1000, 1)
            return primary
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            return ToolStepResult(
                step_id=step.step_id,
                tool_name=step.tool_name,
                status="failed",
                error=ToolInvocationError(TOOL_INTERNAL, "工具计划步骤执行失败", cause=exc),
                latency_ms=round((time.perf_counter() - started) * 1000, 1),
            )

    async def _invoke(
        self,
        *,
        tool_name: str,
        args: dict[str, Any],
        timeout_s: float,
        max_retries: int,
        output_schema: type[BaseModel] | None,
    ) -> ToolStepResult:
        tool = self.registry.get(tool_name)
        if tool is None:
            return ToolStepResult(
                step_id="",
                tool_name=tool_name,
                status="failed",
                error=ToolInvocationError(INVALID_ARGUMENT, "目标工具未注册"),
            )
        policy = ToolExecutionPolicy(
            default_timeout_s=timeout_s,
            default_max_retries=max_retries,
        )
        middleware = ToolHooksMiddleware(
            registry=self.registry,
            metrics_collector=self.metrics_collector,
            policy=policy,
        )

        async def handler(request: ToolCallRequest):
            ainvoke = getattr(tool, "ainvoke", None)
            if callable(ainvoke):
                return await ainvoke(request.tool_call["args"])
            return await asyncio.to_thread(tool.invoke, request.tool_call["args"])

        request = ToolCallRequest(
            tool_call={"name": tool_name, "args": args, "id": f"plan:{tool_name}", "type": "tool_call"},
            tool=tool,
            state=None,
            runtime=None,
        )
        raw = await middleware.awrap_tool_call(request, handler)
        payload = _extract_error_payload(raw)
        if payload:
            code = str(payload.get("code") or TOOL_INTERNAL)
            error = ToolInvocationError(
                code=code,
                public_message=str(payload.get("message") or payload.get("error") or "工具执行失败"),
                retryable=bool(payload.get("retryable", False)),
            )
            return ToolStepResult(
                step_id="",
                tool_name=tool_name,
                status="no_data" if code == NO_DATA else "failed",
                data=payload,
                error=error,
                attempts=max_retries + 1,
                provider=tool_name,
            )

        data = _coerce_data(raw)
        if output_schema is not None:
            try:
                validated = output_schema.model_validate(data)
                data = validated.model_dump()
            except (ValidationError, ValueError, TypeError) as exc:
                return ToolStepResult(
                    step_id="",
                    tool_name=tool_name,
                    status="failed",
                    data=data,
                    error=ToolInvocationError(INVALID_ARGUMENT, "工具输出不符合约定结构", cause=exc),
                    attempts=max_retries + 1,
                    provider=tool_name,
                )
        return ToolStepResult(
            step_id="",
            tool_name=tool_name,
            status="success",
            data=data,
            attempts=max_retries + 1,
            provider=tool_name,
        )

    @staticmethod
    def _timeout_result(step: ToolStep) -> ToolStepResult:
        return ToolStepResult(
            step_id=step.step_id,
            tool_name=step.tool_name,
            status="failed",
            error=ToolInvocationError(TOOL_TIMEOUT, "工具计划执行超时", retryable=True),
        )


def _coerce_data(raw: Any) -> Any:
    content = raw.content if isinstance(raw, ToolMessage) else raw
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError:
                return content
    return content


def _extract_error_payload(raw: Any) -> dict[str, Any] | None:
    content = raw.content if isinstance(raw, ToolMessage) else raw
    if isinstance(content, str):
        stripped = content.strip()
        if not stripped.startswith("{"):
            return None
        try:
            content = json.loads(stripped)
        except json.JSONDecodeError:
            return None
    if isinstance(content, dict) and (content.get("isError") or content.get("error")):
        return content
    return None


__all__ = [
    "ToolFallback",
    "ToolPlan",
    "ToolPlanExecutor",
    "ToolStep",
    "ToolStepResult",
]
