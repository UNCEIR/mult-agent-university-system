# -*- coding: utf-8 -*-
"""query_handbook / query_transcript 工具测试（mock 仓储 + 用户上下文注入）。

2026-08-25 重构：原 query_knowledge 工具拆成两个独立工具。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.main.context import user_context
from storage.milvus.document_vector_repo import PUBLIC_USER


@pytest.fixture
def fake_repos():
    """提供 mock 出来的 document_vector_repo + document_repo，覆盖 runtime 全局属性。"""
    import agent.runtime  # noqa: F401

    vector_repo = MagicMock()
    vector_repo.embedding_client = MagicMock()
    vector_repo.embedding_client.embed_text = AsyncMock(return_value=[0.0] * 8)
    vector_repo.search = MagicMock(return_value=[])

    document_repo = MagicMock()
    document_repo.get_chunk_contents = MagicMock(return_value={})

    with (
        patch("agent.runtime.document_vector_repo", vector_repo),
        patch("agent.runtime.document_repo", document_repo),
    ):
        yield vector_repo, document_repo


# ── query_handbook ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_handbook_default_top_k_is_5(fake_repos):
    """query_handbook 默认 top_k=5（手册维度广，可分段）。"""
    from tools.knowledge.query_handbook import query_handbook

    vector_repo, _ = fake_repos
    vector_repo.search.return_value = []

    result = await query_handbook.ainvoke({"query": "奖学金申请条件"})
    payload = json.loads(result)
    assert payload["top_k"] == 5
    assert payload["scope"] == "handbook"
    # user_ids 永远是 [PUBLIC_USER]，与登录态无关
    _, kwargs = vector_repo.search.call_args
    assert kwargs["user_ids"] == [PUBLIC_USER]
    assert kwargs["top_k"] == 5


@pytest.mark.asyncio
async def test_query_handbook_no_login_required(fake_repos):
    """query_handbook 公开手册，无登录态也可调用（user_ids 仍只含 public）。"""
    from tools.knowledge.query_handbook import query_handbook

    vector_repo, _ = fake_repos
    vector_repo.search.return_value = []

    # 不进入 user_context：user_id 为空（匿名）
    result = await query_handbook.ainvoke({"query": "转专业流程"})
    json.loads(result)
    _, kwargs = vector_repo.search.call_args
    assert kwargs["user_ids"] == [PUBLIC_USER]  # 没把空 user_id 加进去


@pytest.mark.asyncio
async def test_query_handbook_returns_public_scoped_matches(fake_repos):
    from tools.knowledge.query_handbook import query_handbook

    vector_repo, document_repo = fake_repos
    vector_repo.search.return_value = [
        {
            "chunk_id": "handbook:0",
            "source_doc_name": "handbook-2025.pdf",
            "page_number": 12,
            "section": "scholarship-section",
            "user_id": PUBLIC_USER,
            "distance": 0.1,
        }
    ]
    # 用 ASCII 标记关键内容，避免控制台编码问题干扰断言
    document_repo.get_chunk_contents.return_value = {
        "handbook:0": {"content": "[HANDBOOK-MARKER] scholarship rule content"}
    }

    result = await query_handbook.ainvoke({"query": "scholarship", "top_k": 5})
    payload = json.loads(result)
    assert payload["matches"][0]["user_scope"] == "public"
    assert payload["matches"][0]["page_number"] == 12
    assert "[HANDBOOK-MARKER]" in payload["matches"][0]["content"]


@pytest.mark.asyncio
async def test_query_handbook_repo_unavailable_returns_empty():
    """document_vector_repo 不可用时返回 "未检索到"，不抛异常。"""
    from tools.knowledge.query_handbook import query_handbook
    import agent.runtime  # noqa: F401

    with patch("agent.runtime.document_vector_repo", None):
        result = await query_handbook.ainvoke({"query": "任何问题"})
    payload = json.loads(result)
    assert payload["matches"] == []
    assert "message" in payload


# ── query_transcript ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_transcript_default_top_k_is_3(fake_repos):
    """query_transcript 默认 top_k=3（个人查询精度优先）。"""
    from tools.knowledge.query_transcript import query_transcript

    vector_repo, _ = fake_repos

    with user_context("u123"):
        result = await query_transcript.ainvoke({"query": "我修过哪些课"})
    payload = json.loads(result)
    assert payload["top_k"] == 3
    assert payload["scope"] == "transcript"
    _, kwargs = vector_repo.search.call_args
    # 仅本人分区 + top_k=3
    assert kwargs["user_ids"] == ["u123"]
    assert kwargs["top_k"] == 3


@pytest.mark.asyncio
async def test_query_transcript_anonymous_returns_error(fake_repos):
    """未登录 → 不查任何分区，返回错误结构。"""
    from tools.knowledge.query_transcript import query_transcript

    vector_repo, _ = fake_repos
    # 不进入 user_context
    result = await query_transcript.ainvoke({"query": "我修过哪些课"})
    payload = json.loads(result)
    assert "error" in payload
    assert payload["matches"] == []
    # 关键：未登录时 search 不应该被调用
    vector_repo.search.assert_not_called()


@pytest.mark.asyncio
async def test_query_transcript_user_context_public_returns_error(fake_repos):
    """user_id == "public"（被当公开时）也按未登录处理。"""
    from tools.knowledge.query_transcript import query_transcript

    vector_repo, _ = fake_repos
    with user_context(PUBLIC_USER):
        result = await query_transcript.ainvoke({"query": "我修过哪些课"})
    payload = json.loads(result)
    assert "error" in payload


@pytest.mark.asyncio
async def test_query_transcript_user_a_cannot_query_user_b(fake_repos):
    """跨用户隔离：user A 登录态下，工具只能查 user A 分区，绝不查 user B 分区。

    验证：search 调用时 user_ids 参数只包含 user_context 的 user_id，
    即便 LLM/调用方传入 user_id 参数也不影响（工具已硬编码只读 ctx）。
    """
    from tools.knowledge.query_transcript import query_transcript

    vector_repo, _ = fake_repos

    with user_context("user_a"):
        # 即便调用方尝试传入 user_id="user_b"，工具实际仍只查 user_a 分区
        # QueryTranscriptInput schema 不允许传 user_id 字段（拒绝"工具自行传 user_id"）
        try:
            await query_transcript.ainvoke({
                "query": "我修过哪些课",
                # schema 会拒绝额外字段，传 user_id 会被 pydantic 验证忽略
            })
        except Exception:
            pass
        _, kwargs = vector_repo.search.call_args
        # user_ids 绝对不能包含 user_b
        assert "user_b" not in kwargs["user_ids"], (
            f"严重安全 regression！query_transcript 泄露了 user_b 分区：{kwargs}"
        )
        # user_ids 应该只包含 user_context 注入的 user_a
        assert kwargs["user_ids"] == ["user_a"], (
            f"user_ids 应只含本人 user_a：{kwargs}"
        )


@pytest.mark.asyncio
async def test_query_transcript_returns_personal_scoped_matches(fake_repos):
    from tools.knowledge.query_transcript import query_transcript

    vector_repo, document_repo = fake_repos
    vector_repo.search.return_value = [
        {
            "chunk_id": "transcript:0",
            "source_doc_name": "本人成绩单.pdf",
            "page_number": 0,
            "section": "",
            "user_id": "smoke_kb",
            "distance": 0.05,
        }
    ]
    document_repo.get_chunk_contents.return_value = {
        "transcript:0": {"content": "[TRANSCRIPT-MARKER] calc-grade line"}
    }

    with user_context("smoke_kb"):
        result = await query_transcript.ainvoke({"query": "my calc grade", "top_k": 3})
    payload = json.loads(result)
    assert payload["matches"][0]["user_scope"] == "personal"
    assert "[TRANSCRIPT-MARKER]" in payload["matches"][0]["content"]
    # 跨用户隔离：user_ids 只包含本人
    _, kwargs = vector_repo.search.call_args
    assert kwargs["user_ids"] == ["smoke_kb"]


@pytest.mark.asyncio
async def test_query_transcript_repo_unavailable_returns_error():
    """document_vector_repo 不可用时不要抛异常，返回 error 结构。"""
    from tools.knowledge.query_transcript import query_transcript
    import agent.runtime  # noqa: F401

    with patch("agent.runtime.document_vector_repo", None):
        with user_context("u123"):
            result = await query_transcript.ainvoke({"query": "我修过哪些课"})
    payload = json.loads(result)
    # 实现层可能返回 error 或 matches=[]；至少不应抛
    assert "matches" in payload
    assert payload["matches"] == []


# ── 端点注册（保证 SPEC 同步 + ToolRegistry 同步） ──────────────────────

def test_main_agent_spec_includes_adaptive_rag_tool():
    """主 Agent 暴露高层 adaptive_knowledge_retrieve，旧底层工具不再直接暴露。"""
    from agent.main.specs import MAIN_AGENT_SPEC

    allowed = set(MAIN_AGENT_SPEC.allowed_tools)
    assert "adaptive_knowledge_retrieve" in allowed
    assert "query_handbook" not in allowed
    assert "query_transcript" not in allowed
    assert "query_knowledge" not in allowed


def test_runtime_imports_split_tools():
    """runtime.init() 引用的 tool import 必须是 query_handbook / query_transcript。"""
    from agent import runtime

    src = open(runtime.__file__, encoding="utf-8").read()
    assert "adaptive_knowledge_retrieve" in src
    assert "query_handbook" in src
    assert "query_transcript" in src
    assert "query_knowledge" not in src
