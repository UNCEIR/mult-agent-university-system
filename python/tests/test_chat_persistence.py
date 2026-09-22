# -*- coding: utf-8 -*-
"""chat 写纪律 API 层测试：/chat 落库 + 匿名跳过 + AGENTS.md 禁写权限装配。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.api
async def test_chat_persists_turn_and_injects_memory():
    """/chat：首轮注入记忆前缀 + 落库（mock agent + 假 repo）。"""
    from fastapi.testclient import TestClient

    from agent.app import app

    agent = MagicMock()
    agent.ainvoke = AsyncMock(
        return_value={"messages": [MagicMock(content="好的，已记录")]}
    )

    class _FakeRepo:
        def __init__(self):
            self.messages = []

        def session_lock(self, sid):
            return _NoopLock()

        def get_or_create_session(self, sid, uid):
            return {"message_count": 0}

        def append_message(self, sid, uid, role, content, tool_calls_json=None, usage_json=None):
            self.messages.append((role, content))

        def list_memory_entries(self, uid, limit=50, max_chars=2000):
            return [{"kind": "fact", "content": "用户偏好安静"}]

        def count_unextracted(self, sid):
            return 0

        def get_extract_state(self, sid):
            return {"last_extracted_seq": 0, "last_failure_at": 0}

    repo = _FakeRepo()
    with (
        patch("agent.runtime.main_agent", agent),
        patch("agent.runtime.chat_session_repo", repo),
        patch("agent.memory.injector.inject_memory_entries") as inject,
        patch("agent.memory.persistence.persist_turn") as persist,
        patch("agent.memory.extractor.maybe_extract", new=AsyncMock(return_value=False)),
    ):
        inject.return_value = "用户长期记忆：\n- 用户偏好安静"
        client = TestClient(app)
        resp = client.post("/api/v1/chat", json={"message": "帮我记一下我偏好安静", "session_id": "s1", "user_id": "u1"})
    assert resp.status_code == 200
    assert resp.json()["data"]["reply"] == "好的，已记录"
    persist.assert_awaited_once()
    # 注入消息确实作为首条 user 消息传入 agent
    input_msgs = agent.ainvoke.call_args.args[0]["messages"]
    assert input_msgs[0]["content"].startswith("用户长期记忆")


@pytest.mark.api
async def test_chat_injects_image_ids_without_paths_or_base64():
    """图片附件注入主 Agent 的只有 image_id 元数据，不暴露本地路径/base64。"""
    from fastapi.testclient import TestClient

    from agent.app import app

    class _ImageService:
        async def resolve_many(self, image_ids, *, user_id, session_id=None):
            assert image_ids == ["img_abc123456789012345"]
            assert user_id == "u-img"
            assert session_id == "s-img"
            return [
                {
                    "image_id": "img_abc123456789012345",
                    "mime_type": "image/png",
                    "width": 320,
                    "height": 200,
                }
            ]

    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="已收到图片")]})
    with (
        patch("agent.runtime.main_agent", agent),
        patch("agent.runtime.chat_session_repo", None),
        patch("agent.runtime.image_asset_service", _ImageService()),
    ):
        client = TestClient(app)
        resp = client.post(
            "/api/v1/chat",
            json={
                "message": "看看图片",
                "session_id": "s-img",
                "user_id": "u-img",
                "image_ids": ["img_abc123456789012345"],
            },
        )

    assert resp.status_code == 200
    input_msgs = agent.ainvoke.call_args.args[0]["messages"]
    attachment_context = "\n".join(msg["content"] for msg in input_msgs[:-1])
    assert "img_abc123456789012345" in attachment_context
    assert "data:image" not in attachment_context
    assert ".documents" not in attachment_context

class _NoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


@pytest.mark.api
async def test_chat_anonymous_skips_persistence():
    """匿名 user（user_id 空）→ 不落库不注入。"""
    from fastapi.testclient import TestClient

    from agent.app import app

    agent = MagicMock()
    agent.ainvoke = AsyncMock(return_value={"messages": [MagicMock(content="hi")]})
    with (
        patch("agent.runtime.main_agent", agent),
        patch("agent.runtime.chat_session_repo", None),  # 无 repo 时全链路跳过
    ):
        client = TestClient(app)
        resp = client.post("/api/v1/chat", json={"message": "hi", "session_id": "s1", "user_id": ""})
    assert resp.status_code == 200


@pytest.mark.unit
def test_main_agent_permissions_deny_agents_md_write():
    """main agent 装配 FilesystemPermission deny write（AGENTS.md 代码级禁写）。"""
    from unittest.mock import AsyncMock as _A, MagicMock as _M, patch as _P

    settings = _M()
    settings.agent_compaction_trigger_messages = None
    settings.agent_context_window_tokens = 128000
    settings.agent_compaction_keep_tokens = 20000

    backend, checkpointer, llm, compiled = _M(), _M(), _M(), _M()
    with (
        _P("agent.main.factory.get_settings", return_value=settings),
        _P("agent.main.factory.build_agent_backend", return_value=backend),
        _P("agent.main.factory.build_checkpointer", new_callable=_A, return_value=checkpointer),
        _P("agent.main.factory.build_chat_openai", return_value=llm),
        _P("agent.main.factory.create_deep_agent", return_value=compiled) as create,
    ):
        from agent.main.factory import build_deep_agent
        from agent.main.specs import MAIN_AGENT_SPEC

        import asyncio

        asyncio.run(build_deep_agent(MAIN_AGENT_SPEC, tools=[]))

    kwargs = create.call_args.kwargs
    perms = kwargs["permissions"]
    assert perms is not None
    assert perms[0].operations == ["write"]
    assert "/memories/AGENTS.md" in perms[0].paths
    assert perms[0].mode == "deny"
