# -*- coding: utf-8 -*-
"""知识检索工具包 — 学校知识库 RAG 检索（v0.9 重构：手册 vs 成绩单分离）。

- query_handbook：公开手册（user_id=public 分区），无登录也可调用
- query_transcript：个人成绩单（user_id=<current_user> 分区），强制登录隔离

设计动机见 docs/v2.0.0/notes/2026-08-25-knowledge-tools-split.md：
- 把两类不同问题（公开 vs 个人）拆成独立工具，避免 top_k 候选集污染
- 公开 / 个人权限语义清晰
"""

from __future__ import annotations

from .query_handbook import query_handbook
from .adaptive_retrieve import adaptive_knowledge_retrieve
from .query_handbook import query_handbook
from .query_transcript import query_transcript

__all__ = [
    "adaptive_knowledge_retrieve",
    "query_handbook",
    "query_transcript",
]
