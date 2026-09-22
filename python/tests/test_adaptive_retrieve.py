# -*- coding: utf-8 -*-
"""adaptive_knowledge_retrieve：hybrid RRF、权限前置与片段筛选契约。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.main.context import user_context


class _FakeRepo:
    def search_lexical(self, query, user_ids, top_k=20):
        assert user_ids == ["public"]
        return [{
            "chunk_id": "handbook:lex",
            "dataset_id": "handbook",
            "source_doc_name": "学生手册",
            "page_number": 12,
            "section": "奖学金",
            "user_id": "public",
            "lexical_score": 1.0,
        }]

    def get_chunk_contents(self, chunk_ids):
        return {cid: {"content": f"奖学金申请条件内容 {cid}", "page_number": 12, "source_doc_name": "学生手册"} for cid in chunk_ids}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adaptive_retrieve_merges_dense_and_lexical_with_rrf():
    from tools.knowledge.adaptive_retrieve import adaptive_knowledge_retrieve

    plan = {
        "need_retrieval": True,
        "kb_ids": ["handbook"],
        "subqueries": [{"query": "奖学金申请条件", "kb_ids": ["handbook"], "strategy": "hybrid"}],
        "max_rounds": 1,
        "reason_code": "campus_fact",
    }
    dense = [{
        "chunk_id": "handbook:dense",
        "dataset_id": "handbook",
        "source_doc_name": "学生手册",
        "page_number": 11,
        "section": "奖学金",
        "user_id": "public",
        "distance": 0.1,
    }]
    with (
        user_context(""),
        patch("tools.knowledge.adaptive_retrieve._plan", new=AsyncMock(return_value=(plan, "test"))),
        patch("tools.knowledge.adaptive_retrieve._embed_search_chunks", new=AsyncMock(return_value=dense)),
        patch("tools.knowledge.adaptive_retrieve._rerank", new=AsyncMock(return_value=(["handbook:dense", "handbook:lex"], "test"))),
        patch("agent.runtime.document_repo", _FakeRepo()),
    ):
        raw = await adaptive_knowledge_retrieve.ainvoke({"question": "奖学金申请条件", "max_rounds": 1})

    data = json.loads(raw)
    assert data["status"] == "ok"
    assert data["strategy"] == "hybrid"
    assert {item["chunk_id"] for item in data["evidence"]} == {"handbook:dense", "handbook:lex"}
    assert all("user_id" not in item for item in data["evidence"])
    assert len(data["citations"]) == 2


@pytest.mark.unit
def test_lexical_tokens_generate_chinese_bigrams():
    from storage.mysql.document_repo import _lexical_tokens

    tokens = _lexical_tokens("奖学金申请条件")
    assert "奖学" in tokens
    assert "申请" in tokens
    assert "条件" in tokens
