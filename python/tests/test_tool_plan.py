# -*- coding: utf-8 -*-
"""ToolPlanExecutor 契约测试：并行、串行依赖、fallback、allowlist 与 deadline。"""

from __future__ import annotations

import asyncio
import time

import pytest
from pydantic import BaseModel

from agent.main.tool_plan import ToolFallback, ToolPlan, ToolPlanExecutor, ToolStep
from tools.circuit_breaker import CircuitBreaker


class _Tool:
    def __init__(self, fn):
        self._fn = fn

    async def ainvoke(self, args):
        result = self._fn(args)
        if asyncio.iscoroutine(result):
            return await result
        return result


class _Registry:
    def __init__(self, tools: dict[str, _Tool], internal: set[str] | None = None):
        self._tools = tools
        self._internal = internal or set()
        self._breakers = {name: CircuitBreaker() for name in tools}

    def get(self, name):
        return self._tools.get(name)

    def is_internal(self, name):
        return name in self._internal


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dependency_free_steps_run_parallel():
    both_started = asyncio.Event()
    started = 0
    lock = asyncio.Lock()

    async def parallel_tool(args):
        nonlocal started
        async with lock:
            started += 1
            if started == 2:
                both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        return {"name": args["name"]}

    registry = _Registry({"t1": _Tool(parallel_tool), "t2": _Tool(parallel_tool)})
    plan = ToolPlan(
        plan_id="parallel",
        allowed_tools=frozenset({"t1", "t2"}),
        steps=(
            ToolStep(step_id="a", tool_name="t1", build_args=lambda _: {"name": "a"}),
            ToolStep(step_id="b", tool_name="t2", build_args=lambda _: {"name": "b"}),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["a"].status == "success"
    assert results["b"].status == "success"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_one_failure_does_not_block_independent_sibling():
    async def failing(args):
        raise RuntimeError("boom")

    async def healthy(args):
        return {"ok": True}

    registry = _Registry({"bad": _Tool(failing), "good": _Tool(healthy)})
    plan = ToolPlan(
        plan_id="isolated",
        allowed_tools=frozenset({"bad", "good"}),
        steps=(
            ToolStep(step_id="bad", tool_name="bad", build_args=lambda _: {}),
            ToolStep(step_id="good", tool_name="good", build_args=lambda _: {}),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["bad"].status == "failed"
    assert results["good"].status == "success"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_serial_step_receives_upstream_data():
    registry = _Registry(
        {
            "a": _Tool(lambda args: {"value": 7}),
            "b": _Tool(lambda args: {"doubled": args["value"] * 2}),
        }
    )
    plan = ToolPlan(
        plan_id="serial",
        allowed_tools=frozenset({"a", "b"}),
        steps=(
            ToolStep(step_id="a", tool_name="a", build_args=lambda _: {}),
            ToolStep(
                step_id="b",
                tool_name="b",
                depends_on=("a",),
                build_args=lambda out: {"value": out["a"].data["value"]},
            ),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["b"].data == {"doubled": 14}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dependency_failure_skips_downstream_without_fake_data():
    called = {"b": 0}

    async def failing(args):
        raise RuntimeError("boom")

    def downstream(args):
        called["b"] += 1
        return {}

    registry = _Registry({"a": _Tool(failing), "b": _Tool(downstream)})
    plan = ToolPlan(
        plan_id="skip",
        allowed_tools=frozenset({"a", "b"}),
        steps=(
            ToolStep(step_id="a", tool_name="a", build_args=lambda _: {}),
            ToolStep(step_id="b", tool_name="b", depends_on=("a",), build_args=lambda _: {}),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["a"].status == "failed"
    assert results["b"].status == "skipped"
    assert called["b"] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fallback_success_allows_downstream_to_continue():
    async def failing(args):
        raise ConnectionError("network")

    registry = _Registry(
        {
            "primary": _Tool(failing),
            "fallback": _Tool(lambda args: {"value": 9}),
            "downstream": _Tool(lambda args: {"received": args["value"]}),
        }
    )
    plan = ToolPlan(
        plan_id="fallback",
        allowed_tools=frozenset({"primary", "fallback", "downstream"}),
        steps=(
            ToolStep(
                step_id="a",
                tool_name="primary",
                build_args=lambda _: {},
                fallback=ToolFallback(
                    tool_name="fallback",
                    build_args=lambda _: {},
                    timeout_s=1,
                ),
            ),
            ToolStep(
                step_id="b",
                tool_name="downstream",
                depends_on=("a",),
                build_args=lambda out: {"value": out["a"].data["value"]},
            ),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["a"].status == "fallback_success"
    assert results["a"].fallback_used is True
    assert results["b"].data == {"received": 9}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_data_is_distinct_status():
    registry = _Registry(
        {
            "empty": _Tool(
                lambda args: '{"isError": true, "code": "NO_DATA", "message": "none"}'
            )
        }
    )
    plan = ToolPlan(
        plan_id="empty",
        allowed_tools=frozenset({"empty"}),
        steps=(ToolStep(step_id="a", tool_name="empty", build_args=lambda _: {}),),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["a"].status == "no_data"


@pytest.mark.unit
def test_plan_rejects_unallowed_or_internal_tool():
    registry = _Registry({"secret": _Tool(lambda args: {}), "ok": _Tool(lambda args: {})}, internal={"secret"})
    executor = ToolPlanExecutor(registry)
    with pytest.raises(ValueError, match="not allowed"):
        executor.validate(
            ToolPlan(
                plan_id="forbidden",
                allowed_tools=frozenset({"ok"}),
                steps=(ToolStep(step_id="s", tool_name="secret", build_args=lambda _: {}),),
            )
        )
    with pytest.raises(ValueError, match="internal"):
        executor.validate(
            ToolPlan(
                plan_id="internal",
                allowed_tools=frozenset({"secret"}),
                steps=(ToolStep(step_id="s", tool_name="secret", build_args=lambda _: {}),),
            )
        )


@pytest.mark.unit
def test_plan_rejects_duplicate_missing_and_cycle():
    registry = _Registry({"t": _Tool(lambda args: {})})
    executor = ToolPlanExecutor(registry)
    with pytest.raises(ValueError, match="duplicate"):
        executor.validate(
            ToolPlan(
                plan_id="dup",
                allowed_tools=frozenset({"t"}),
                steps=(
                    ToolStep(step_id="x", tool_name="t", build_args=lambda _: {}),
                    ToolStep(step_id="x", tool_name="t", build_args=lambda _: {}),
                ),
            )
        )
    with pytest.raises(ValueError, match="unknown dependencies"):
        executor.validate(
            ToolPlan(
                plan_id="missing",
                allowed_tools=frozenset({"t"}),
                steps=(ToolStep(step_id="x", tool_name="t", depends_on=("nope",), build_args=lambda _: {}),),
            )
        )
    with pytest.raises(ValueError, match="cycle"):
        executor.validate(
            ToolPlan(
                plan_id="cycle",
                allowed_tools=frozenset({"t"}),
                steps=(
                    ToolStep(step_id="a", tool_name="t", depends_on=("b",), build_args=lambda _: {}),
                    ToolStep(step_id="b", tool_name="t", depends_on=("a",), build_args=lambda _: {}),
                ),
            )
        )


class _Output(BaseModel):
    value: int


@pytest.mark.unit
@pytest.mark.asyncio
async def test_output_schema_validation_failure_is_failed():
    registry = _Registry({"bad_schema": _Tool(lambda args: {"value": "not-int"})})
    plan = ToolPlan(
        plan_id="schema",
        allowed_tools=frozenset({"bad_schema"}),
        steps=(
            ToolStep(
                step_id="a",
                tool_name="bad_schema",
                build_args=lambda _: {},
                output_schema=_Output,
            ),
        ),
    )
    results = await ToolPlanExecutor(registry).run(plan)
    assert results["a"].status == "failed"
    assert results["a"].error.code == "INVALID_ARGUMENT"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_global_deadline_marks_remaining_steps_failed():
    async def slow(args):
        await asyncio.sleep(0.1)
        return {}

    registry = _Registry({"slow": _Tool(slow)})
    plan = ToolPlan(
        plan_id="deadline",
        allowed_tools=frozenset({"slow"}),
        deadline_s=0.02,
        steps=(ToolStep(step_id="a", tool_name="slow", timeout_s=1, build_args=lambda _: {}),),
    )
    started = time.perf_counter()
    results = await ToolPlanExecutor(registry).run(plan)
    assert time.perf_counter() - started < 0.08
    assert results["a"].status == "failed"
    assert results["a"].error.code == "TOOL_TIMEOUT"
