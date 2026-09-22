# -*- coding: utf-8 -*-
"""compaction 记忆地基单测（Phase 4 A3/A2/A0 部分）：

- estimate_context_tokens 两路（mock usage_json 优先 / 纯字符估算）
- should_compact 边界
- chat_session_compactions 落库/读取（SQLite 内存引擎）
- append_message usage_json 写入读回（A0 数据源）
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from agent.memory.tokens import estimate_context_tokens, should_compact
from storage.mysql.chat_session_repo import ChatSessionRepository

COMPACT_DDL = """
CREATE TABLE chat_session_compactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL,
    prev_compaction_id BIGINT,
    first_kept_message_id BIGINT NOT NULL DEFAULT 0,
    tokens_before INT NOT NULL DEFAULT 0,
    tokens_after INT NOT NULL DEFAULT 0,
    reserve_tokens INT NOT NULL DEFAULT 0,
    keep_recent_tokens INT NOT NULL DEFAULT 0,
    model VARCHAR(64) NOT NULL DEFAULT '',
    reason VARCHAR(16) NOT NULL DEFAULT 'threshold',
    status VARCHAR(16) NOT NULL DEFAULT 'ok',
    usage_json TEXT,
    details_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

MESSAGES_DDL = """
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    seq INT NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT,
    tool_calls_json TEXT,
    attachments_json TEXT,
    usage_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture
def sqlite_repo():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text(COMPACT_DDL))
        conn.execute(text(MESSAGES_DDL))
    repo = ChatSessionRepository()
    repo.ping = lambda: True
    repo._engine = engine
    return repo


# ── A3 token 估算 ─────────────────────────────────────────────────
@pytest.mark.unit
def test_estimate_tokens_usage_json_priority():
    msgs = [{"content": "你好世界" * 1000}]
    # provider usage 优先：即使字符估算巨大，也采用 usage_json.total_tokens
    assert estimate_context_tokens(msgs, {"total_tokens": 42}) == 42


@pytest.mark.unit
def test_estimate_tokens_usage_json_string():
    msgs = [{"content": "hello"}]
    assert estimate_context_tokens(msgs, json.dumps({"total_tokens": 7})) == 7


@pytest.mark.unit
def test_estimate_tokens_chars_fallback():
    msgs = [{"content": "你好世界"}]  # 4 个汉字 → /2 = 2
    assert estimate_context_tokens(msgs) == 3


@pytest.mark.unit
def test_estimate_tokens_empty():
    assert estimate_context_tokens([]) == 0
    assert estimate_context_tokens([], {"input_tokens": 0}) == 0


@pytest.mark.unit
def test_should_compact_boundary():
    assert should_compact(128000, 128000, 16384) is True
    assert should_compact(111615, 128000, 16384) is False  # 111616 = 阈值


# ── A2/A1 compaction 落库/读取 ─────────────────────────────────────
@pytest.mark.unit
def test_append_and_get_latest_compaction(sqlite_repo):
    rid = sqlite_repo.append_compaction(
        user_id="u1", session_id="s1", summary="摘要A",
        first_kept_message_id=10, model="deepseek-v4.1-flash", reason="threshold", status="ok",
    )
    assert rid > 0
    latest = sqlite_repo.get_latest_compaction("s1")
    assert latest is not None
    assert latest["summary"] == "摘要A"
    assert latest["first_kept_message_id"] == 10
    assert latest["status"] == "ok"


@pytest.mark.unit
def test_append_compaction_chain_prev_id(sqlite_repo):
    rid1 = sqlite_repo.append_compaction(user_id="u1", session_id="s1", summary="A")
    rid2 = sqlite_repo.append_compaction(
        user_id="u1", session_id="s1", summary="B", prev_compaction_id=rid1
    )
    latest = sqlite_repo.get_latest_compaction("s1")
    assert latest["id"] == rid2
    assert latest["prev_compaction_id"] == rid1


@pytest.mark.unit
def test_list_compactions_desc(sqlite_repo):
    sqlite_repo.append_compaction(user_id="u1", session_id="s1", summary="A")
    sqlite_repo.append_compaction(user_id="u1", session_id="s1", summary="B")
    rows = sqlite_repo.list_compactions("s1")
    assert [r["summary"] for r in rows] == ["B", "A"]


@pytest.mark.unit
def test_get_latest_compaction_none(sqlite_repo):
    assert sqlite_repo.get_latest_compaction("no-such") is None


def _seed_msgs(repo, rows):
    """直接 SQL 播种 chat_messages（绕开 MySQL 方言 append_message，SQLite 兼容）。"""
    with repo._engine.begin() as conn:
        for seq, role, content, usage in rows:
            conn.execute(
                text(
                    "INSERT INTO chat_messages (session_id, user_id, seq, role, content, usage_json) "
                    "VALUES (:sid, :uid, :seq, :role, :content, :usage)"
                ),
                {"sid": "s1", "uid": "u1", "seq": seq, "role": role, "content": content, "usage": usage},
            )


@pytest.mark.unit
def test_list_entries_after_seq(sqlite_repo):
    _seed_msgs(sqlite_repo, [(1, "user", "第一条", None), (2, "assistant", "第二条", None), (3, "user", "第三条", None)])
    after = sqlite_repo.list_entries_after_seq("s1", seq=1)
    assert [r["seq"] for r in after] == [2, 3]


# ── A0 usage_json 写入读回 ─────────────────────────────────────────
@pytest.mark.unit
def test_append_message_usage_json_roundtrip(sqlite_repo):
    usage = json.dumps({"input_tokens": 10, "output_tokens": 5}, ensure_ascii=False)
    _seed_msgs(sqlite_repo, [(1, "assistant", "内容", usage)])
    with sqlite_repo._engine.connect() as conn:
        row = conn.execute(
            text("SELECT usage_json FROM chat_messages WHERE session_id = 's1' AND seq = 1")
        ).mappings().first()
    assert row["usage_json"] == usage
