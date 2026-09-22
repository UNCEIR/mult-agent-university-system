# -*- coding: utf-8 -*-
"""知识检索工具的公共 helpers — 拆 query_knowledge 时抽出复用层。

- `_embed_search_chunks(user_ids, query, top_k)`: embed + Milvus search
- `_assemble_matches(hits, contents_map)`: 把 hit + content 装成 LLM 看到的 match dict
- `_format_query_tool_result(matches_or_error)`: 把列表 / 错误统一成 JSON 字符串

被 query_handbook / query_transcript 两个工具共享。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent import runtime
from storage.milvus.document_vector_repo import PUBLIC_USER


async def _embed_search_chunks(
    user_ids: list[str],
    query: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """公共：query → embed → Milvus search → 返回 hit 元信息列表（不含 content）。

    严格遵循 user_ids 列表做 partition_key 过滤（user_ids 之间不会互相污染）。
    若 document_vector_repo 不可用 / embedding 失败 / search 抛错 → 返回空 list（由调用方
    自行决定返回 "未检索到" 或带 error 字段的 JSON）。
    """
    repo = getattr(runtime, "document_vector_repo", None)
    if repo is None:
        return []
    if not user_ids:
        return []

    # 计算 query 向量
    query_vector: list[float] | None = None
    try:
        query_vector = repo.embedding_client.embed_text(query)
    except Exception:  # noqa: BLE001
        return []

    # search
    try:
        hits = repo.search(
            query,
            top_k=top_k,
            user_ids=user_ids,
            query_vector=query_vector,
        )
        return list(hits)
    except Exception:  # noqa: BLE001
        return []


async def _assemble_matches(
    hits: list[dict[str, Any]],
    content_limit: int = 800,
) -> list[dict[str, Any]]:
    """把 hit 元信息 + MySQL `document_chunks` 的 content 装配成 LLM 可见的 match dict。

    每条 match 包含：rank / chunk_id / source_doc_name / page_number / section /
    user_scope / score / content。
    """
    if not hits:
        return []

    document_repo = getattr(runtime, "document_repo", None)
    contents: dict[str, dict] = {}
    if document_repo is not None:
        try:
            chunk_ids = [hit["chunk_id"] for hit in hits]
            contents = await asyncio.to_thread(document_repo.get_chunk_contents, chunk_ids)
        except Exception:  # noqa: BLE001
            contents = {}

    matches: list[dict[str, Any]] = []
    for idx, hit in enumerate(hits):
        chunk_id = hit["chunk_id"]
        content = contents.get(chunk_id, {}).get("content", "")
        matches.append(
            {
                "rank": idx + 1,
                "chunk_id": chunk_id,
                "source_doc_name": hit.get("source_doc_name", ""),
                "page_number": hit.get("page_number", 0),
                "section": hit.get("section", ""),
                "user_scope": "public" if hit.get("user_id", "") == PUBLIC_USER else "personal",
                "score": round(float(1.0 - hit.get("distance", 1.0)), 4),
                "content": content[:content_limit],
            }
        )
    return matches


def _format_tool_result(
    query: str,
    top_k: int,
    matches: list[dict[str, Any]],
    *,
    user_id: str,
    scope: str,
) -> str:
    """把 matches 序列化成 JSON 字符串给 LLM。无 hits 时带 "未检索到" 友好提示。"""
    if not matches:
        return json.dumps(
            {
                "query": query,
                "scope": scope,
                "top_k": top_k,
                "matches": [],
                "message": f"未在{('公开学生手册' if scope == 'handbook' else '个人成绩单')}中检索到相关内容",
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "query": query,
            "scope": scope,
            "top_k": top_k,
            "matches": matches,
            "user_id": user_id,
        },
        ensure_ascii=False,
    )


__all__ = ["_embed_search_chunks", "_assemble_matches", "_format_tool_result"]
