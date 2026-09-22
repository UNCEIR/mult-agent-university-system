# -*- coding: utf-8 -*-
"""Chat SSE 工具事件跟踪器。

以 LangGraph ``tools`` 节点的输入/最终 messages 为权威来源，按 ``tool_call_id``
关联工具调用；``on_tool_start/end`` 只补充 latency/run_id。输出仍保持
``status=start|end``，失败通过 ``ok=False + code/message`` 表达，兼容现有前端枚举。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

_SAFE_ARG_KEYS = {
    "intent",
    "mode",
    "top_k",
    "max_results",
    "language",
    "timeout",
    "strategy",
}
_SENSITIVE_ARG_KEYS = {"query", "message", "prompt", "content", "question", "text"}


@dataclass
class _ToolCallState:
    tool_call_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    started_at: float = field(default_factory=time.monotonic)
    run_id: str | None = None
    start_emitted: bool = False
    ended: bool = False
    error: tuple[str, str] | None = None


class ChatToolEventTracker:
    """把 LangGraph v1 events 转换为前端可消费的工具 SSE payload。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._calls: dict[str, _ToolCallState] = {}
        self._run_to_call: dict[str, str] = {}
        # on_tool_start 可能早于 tools 节点输入。这里只登记临时 run，
        # 等 canonical tool_call_id 到达后再迁移，绝不为同一次调用生成第二只工具。
        self._pending_runs: dict[str, _ToolCallState] = {}

    def handle(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        kind = str(event.get("event", "") or "")
        if kind == "on_chain_start" and _is_tools_node(event):
            return self._start_from_node_input(event)
        if kind == "on_tool_start":
            return self._start_from_tool_event(event)
        if kind == "on_tool_end":
            self._record_tool_end(event)
            return []
        if kind == "on_tool_error":
            self._record_tool_error(event)
            return []
        if kind == "on_chain_end" and _is_tools_node(event):
            return self._finish_from_node_output(event)
        if kind == "on_chain_error" and _is_tools_node(event):
            return self._fail_pending("TOOL_NODE_ERROR", "工具节点执行失败")
        return []

    def fail_pending(self, code: str, message: str) -> list[dict[str, Any]]:
        return self._fail_pending(code, message)

    def _start_from_node_input(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        messages = _extract_messages((event.get("data") or {}).get("input"))
        tool_calls = _last_tool_calls(messages)
        payloads: list[dict[str, Any]] = []
        for index, tc in enumerate(tool_calls):
            call_id = str(_get(tc, "id") or f"tool_call_{index}")
            tool = str(_get(tc, "name") or "")
            state = self._calls.get(call_id)
            if state is None:
                state = _ToolCallState(
                    tool_call_id=call_id,
                    tool=tool,
                    args=_safe_args_summary(_get(tc, "args") or {}),
                )
                self._calls[call_id] = state
            else:
                state.tool = tool or state.tool
                state.args = _safe_args_summary(_get(tc, "args") or state.args)
            if not state.start_emitted:
                state.start_emitted = True
                payloads.append(self._start_payload(state))
        return payloads

    def _start_from_tool_event(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        run_id = str(event.get("run_id") or "")
        tool = str(event.get("name", "") or "")
        data_input = (event.get("data") or {}).get("input")
        args = data_input if isinstance(data_input, dict) else {}

        state = self._resolve_run_state(run_id=run_id, tool=tool)
        if state is not None:
            state.started_at = time.monotonic()
            state.run_id = run_id or state.run_id
            return []

        # 不立即下发。真正的用户可见 start 由 tools 节点的 canonical call_id 产生；
        # 如果 canonical start 始终没有出现，该临时 run 最终会被丢弃而不是误报失败。
        pending_key = run_id or f"tool_run_{len(self._pending_runs)}"
        self._pending_runs[pending_key] = _ToolCallState(
            tool_call_id=pending_key,
            tool=tool,
            args=_safe_args_summary(args),
            run_id=run_id or None,
        )
        return []

    def _record_tool_end(self, event: dict[str, Any]) -> None:
        run_id = str(event.get("run_id") or "")
        tool = str(event.get("name", "") or "")
        state = self._resolve_run_state(run_id=run_id, tool=tool)
        if state is None:
            return

    def _record_tool_error(self, event: dict[str, Any]) -> None:
        run_id = str(event.get("run_id") or "")
        tool = str(event.get("name", "") or "")
        state = self._resolve_run_state(run_id=run_id, tool=tool)
        if state is None:
            return
        exc = (event.get("data") or {}).get("error")
        code = str(getattr(exc, "code", "TOOL_INTERNAL") or "TOOL_INTERNAL")
        state.error = (code, "工具执行失败")

    def _finish_from_node_output(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        output = (event.get("data") or {}).get("output")
        messages = _extract_messages(output)
        payloads: list[dict[str, Any]] = []
        matched: set[str] = set()

        for msg in messages:
            call_id = str(_get(msg, "tool_call_id") or "")
            if not call_id:
                continue
            state = self._calls.get(call_id)
            if state is None:
                tool = str(_get(msg, "name") or "")
                state = _ToolCallState(
                    tool_call_id=call_id,
                    tool=tool,
                    start_emitted=True,
                    started_at=time.monotonic(),
                )
                self._attach_pending_run(state)
                self._calls[call_id] = state
            if state.ended:
                continue
            matched.add(call_id)
            payloads.append(self._end_from_message(state, msg))

        for state in self._calls.values():
            if state.tool_call_id in matched or state.ended:
                continue
            if state.start_emitted:
                code, message = state.error or ("TOOL_UNFINISHED", "工具未返回结果")
                payloads.append(self._end_payload(state, ok=False, code=code, message=message))
                state.ended = True
        return payloads

    def _end_from_message(self, state: _ToolCallState, message: Any) -> dict[str, Any]:
        state.ended = True
        payload = _error_payload_from_message(message)
        if payload:
            return self._end_payload(
                state,
                ok=False,
                code=str(payload.get("code") or "TOOL_INTERNAL"),
                message=str(payload.get("message") or payload.get("error") or "工具执行失败"),
                retryable=bool(payload.get("retryable", False)),
            )
        return self._end_payload(state, ok=True)

    def _fail_pending(self, code: str, message: str) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for state in self._calls.values():
            if state.ended or not state.start_emitted:
                continue
            state.ended = True
            payloads.append(self._end_payload(state, ok=False, code=code, message=message))
        return payloads

    def _resolve_run_state(self, *, run_id: str, tool: str) -> _ToolCallState | None:
        """按 run_id 优先解析；缺 canonical 映射时只绑定唯一未分配的同类调用。"""
        if run_id and run_id in self._run_to_call:
            return self._calls.get(self._run_to_call[run_id])
        if run_id and run_id in self._pending_runs:
            return self._pending_runs[run_id]

        candidates = [
            state
            for state in self._calls.values()
            if not state.ended and state.tool == tool and not state.run_id
        ]
        if not candidates:
            return None
        state = candidates[0]
        if run_id:
            state.run_id = run_id
            self._run_to_call[run_id] = state.tool_call_id
        return state

    def _attach_pending_run(self, state: _ToolCallState) -> None:
        """canonical call 出现后，将更早到达的同名临时 run 迁移到它。"""
        if state.run_id:
            return
        for run_id, pending in list(self._pending_runs.items()):
            if pending.tool != state.tool:
                continue
            state.run_id = run_id
            state.started_at = pending.started_at
            self._run_to_call[run_id] = state.tool_call_id
            self._pending_runs.pop(run_id, None)
            return

    def _start_payload(self, state: _ToolCallState) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": state.tool,
            "status": "start",
            "session_id": self.session_id,
            "tool_call_id": state.tool_call_id,
            "args": state.args,
        }
        if state.run_id:
            payload["run_id"] = state.run_id
        return payload

    def _end_payload(
        self,
        state: _ToolCallState,
        *,
        ok: bool,
        code: str | None = None,
        message: str | None = None,
        retryable: bool | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tool": state.tool,
            "status": "end",
            "session_id": self.session_id,
            "tool_call_id": state.tool_call_id,
            "ok": ok,
            "latency_ms": round((time.monotonic() - state.started_at) * 1000, 1),
        }
        if state.run_id:
            payload["run_id"] = state.run_id
        if code:
            payload["code"] = code
        if message:
            payload["message"] = _truncate(message, 240)
        if retryable is not None:
            payload["retryable"] = retryable
        return payload


def _is_tools_node(event: dict[str, Any]) -> bool:
    return str(event.get("name", "") or "") == "tools"


def _extract_messages(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        messages = value.get("messages")
        return list(messages) if isinstance(messages, (list, tuple)) else []
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and isinstance(value[1], dict) and "messages" in value[1]:
            return _extract_messages(value[1])
        return list(value)
    messages = getattr(value, "messages", None)
    return list(messages) if isinstance(messages, (list, tuple)) else []


def _last_tool_calls(messages: list[Any]) -> list[Any]:
    for message in reversed(messages):
        calls = _get(message, "tool_calls")
        if calls:
            return list(calls)
    return []


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _safe_args_summary(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {}
    summary: dict[str, Any] = {}
    for key, value in args.items():
        key_text = str(key)
        if key_text in _SAFE_ARG_KEYS:
            if isinstance(value, (str, int, float, bool)) or value is None:
                summary[key_text] = _truncate(str(value), 80) if isinstance(value, str) else value
        elif key_text in _SENSITIVE_ARG_KEYS:
            summary[f"{key_text}_chars"] = len(str(value or ""))
    return summary


def _error_payload_from_message(message: Any) -> dict[str, Any] | None:
    status = str(_get(message, "status", "") or "")
    content = _get(message, "content", message)
    payload: Any = content
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = None
    if isinstance(payload, dict) and (payload.get("isError") or payload.get("error")):
        return payload
    if status == "error":
        return {"code": "TOOL_INTERNAL", "message": "工具执行失败"}
    return None


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[:limit]}…"


__all__ = ["ChatToolEventTracker"]
