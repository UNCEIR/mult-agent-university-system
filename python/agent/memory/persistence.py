# -*- coding: utf-8 -*-
"""chat 写纪律（pi 移植）：turn 消息逐条落库 + 匿名跳过 + 尽力而为。

- persist_turn：user 消息 + assistant 消息（含工具调用）逐条 append，
  每条独立提交（中断不丢已落）；MySQL 不可用 → 告警不阻塞对话
- 崩溃保守：seq 原子自增，重复调用幂等由调用方控制（每 turn 只调一次）
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _content_of(message) -> str:
    if isinstance(message, dict):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def _tool_calls_of(message) -> str | None:
    """assistant 消息的工具调用（审计用）→ JSON 字符串。"""
    calls = getattr(message, "tool_calls", None)
    if calls:
        try:
            return json.dumps([{"name": c.get("name", ""), "args": c.get("args", {})} for c in calls], ensure_ascii=False)
        except Exception:  # noqa: BLE001
            return None
    return None


def _usage_of(message, usage_metadata: dict | str | None = None) -> str | None:
    """提取消息 usage → JSON 字符串（A0：provider usage 优先数据源）。

    优先级：显式传入 usage_metadata > 消息自带 usage_metadata（AIMessage）。
    均缺失返回 None（不写 usage_json 列）。
    """
    um = usage_metadata
    if not um:
        um = getattr(message, "usage_metadata", None) or {}
    if not um:
        return None
    if isinstance(um, str):
        return um
    if isinstance(um, dict):
        try:
            return json.dumps(um, ensure_ascii=False)
        except (TypeError, ValueError):  # noqa: BLE001
            return None
    return None


async def persist_turn(
    repo,
    *,
    session_id: str,
    user_id: str,
    user_msg: str,
    assistant_msgs: list | None = None,
    user_attachments: list | None = None,
    usage_metadata: dict | str | None = None,
) -> None:
    """把一轮对话落库（user + assistant 逐条；匿名 user 跳过）。

    Phase 4（A0）：assistant 消息落库时写入 usage_json —— 显式传入的
    usage_metadata 优先（流式路径由 chat.py 聚合后传入），否则从消息自带
    usage_metadata 提取（非流式路径 AIMessage）。供 A3 token 估算与 C1 成本记账。
    """
    if not user_id:
        logger.debug("persist_turn skipped (anonymous user)")
        return
    try:
        lock = repo.session_lock(session_id)
        async with lock:
            attachments_json = None
            if user_attachments:
                try:
                    attachments_json = json.dumps(user_attachments, ensure_ascii=False)
                except (TypeError, ValueError):  # noqa: BLE001
                    attachments_json = None
            if attachments_json:
                repo.append_message(
                    session_id,
                    user_id,
                    "user",
                    user_msg,
                    attachments_json=attachments_json,
                )
            else:
                repo.append_message(session_id, user_id, "user", user_msg)
            for msg in assistant_msgs or []:
                role = "assistant"
                tool_calls = _tool_calls_of(msg)
                repo.append_message(
                    session_id,
                    user_id,
                    role,
                    _content_of(msg),
                    tool_calls_json=tool_calls,
                    usage_json=_usage_of(msg, usage_metadata),
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist_turn failed (best effort): %s", exc)
