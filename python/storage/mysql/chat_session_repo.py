"""chat 会话/记忆仓储 — MySQL chat_sessions / chat_messages / chat_memory_entries。

表结构由 sql/init-db.sql 定义，本仓储只做 CRUD，不建表。
写纪律（pi 移植）：
- append-only：chat_messages 只增不删，seq 事务内原子自增分配（并发安全）
- (session_id, user_id) 复合键：防跨用户串会话
- content_hash 唯一索引：NFKC 归一精确去重（记忆条目）
"""

from __future__ import annotations

import asyncio
import hashlib
import unicodedata
from datetime import datetime, timezone

import structlog


def _utcnow_naive() -> datetime:
    """naive UTC 当前时间（避免 MySQL/SQLite 时区差异）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)
from sqlalchemy import bindparam, text

from .base import MySQLRepository

logger = structlog.get_logger()


class ChatSessionRepository(MySQLRepository):
    """chat 会话记录 + 跨会话记忆条目 CRUD。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._locks: dict[str, asyncio.Lock] = {}
        self._attachments_column: bool | None = None

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """per-session 并发锁（seq 分配与提取水位串行）。"""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def _has_attachments_column(self) -> bool:
        """兼容旧库：attachments_json 未迁移时不阻断会话读写。"""
        if self._attachments_column is not None:
            return self._attachments_column
        assert self._engine is not None
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT attachments_json FROM chat_messages LIMIT 0"))
            self._attachments_column = True
        except Exception:  # noqa: BLE001
            self._attachments_column = False
        return self._attachments_column

    # ── chat_sessions ─────────────────────────────────────────────────
    def get_or_create_session(self, session_id: str, user_id: str) -> dict:
        """以 (session_id, user_id) 复合键取会话；不匹配则新开（防串会话）。"""
        if not self.ping():
            return {"session_id": session_id, "user_id": user_id, "message_count": 0, "last_extracted_seq": 0}
        assert self._engine is not None
        with self._engine.begin() as conn:
            row = conn.execute(
                text("SELECT session_id, user_id, message_count, last_extracted_seq, status FROM chat_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).mappings().first()
            if row and row["user_id"] == user_id:
                return dict(row)
            # 新开会话（不存在或 user 不匹配）
            conn.execute(
                text(
                    "INSERT INTO chat_sessions (session_id, user_id) VALUES (:sid, :uid) "
                    "ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)"
                ),
                {"sid": session_id, "uid": user_id},
            )
        return {"session_id": session_id, "user_id": user_id, "message_count": 0, "last_extracted_seq": 0}

    def append_message(self, session_id: str, user_id: str, role: str, content: str, tool_calls_json=None, attachments_json=None, usage_json=None) -> int:
        """追加消息：事务内原子自增分配 seq + message_count。返回 seq。"""
        if not self.ping():
            return -1
        assert self._engine is not None
        with self._engine.begin() as conn:
            conn.execute(
                text("INSERT INTO chat_sessions (session_id, user_id) VALUES (:sid, :uid) ON DUPLICATE KEY UPDATE user_id = VALUES(user_id)"),
                {"sid": session_id, "uid": user_id},
            )
            row = conn.execute(
                text("SELECT message_count FROM chat_sessions WHERE session_id = :sid FOR UPDATE"),
                {"sid": session_id},
            ).mappings().first()
            seq = int(row["message_count"]) + 1 if row else 1
            if self._has_attachments_column():
                conn.execute(
                    text(
                        "INSERT INTO chat_messages (session_id, user_id, seq, role, content, tool_calls_json, attachments_json, usage_json) "
                        "VALUES (:sid, :uid, :seq, :role, :content, :tool_calls, :attachments, :usage)"
                    ),
                    {
                        "sid": session_id,
                        "uid": user_id,
                        "seq": seq,
                        "role": role,
                        "content": content,
                        "tool_calls": tool_calls_json,
                        "attachments": attachments_json,
                        "usage": usage_json,
                    },
                )
            else:
                conn.execute(
                    text(
                        "INSERT INTO chat_messages (session_id, user_id, seq, role, content, tool_calls_json, usage_json) "
                        "VALUES (:sid, :uid, :seq, :role, :content, :tool_calls, :usage)"
                    ),
                    {
                        "sid": session_id,
                        "uid": user_id,
                        "seq": seq,
                        "role": role,
                        "content": content,
                        "tool_calls": tool_calls_json,
                        "usage": usage_json,
                    },
                )
            conn.execute(
                text("UPDATE chat_sessions SET message_count = :cnt WHERE session_id = :sid"),
                {"cnt": seq, "sid": session_id},
            )
        return seq

    def list_messages(self, session_id: str, after_seq: int = 0, limit: int = 500) -> list[dict]:
        if not self.ping():
            return []
        assert self._engine is not None
        columns = "seq, role, content, tool_calls_json, attachments_json" if self._has_attachments_column() else "seq, role, content, tool_calls_json"
        sql = text(
            f"SELECT {columns} FROM chat_messages "
            "WHERE session_id = :sid AND seq > :after ORDER BY seq LIMIT :limit"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"sid": session_id, "after": after_seq, "limit": limit}).mappings().all()
        result = []
        for row in rows:
            item = dict(row)
            item.setdefault("attachments_json", None)
            result.append(item)
        return result

    def list_sessions_by_user(self, user_id: str, limit: int = 100) -> list[dict]:
        """按用户列会话（active），title 为空时取该会话首条 user 消息作显示名。"""
        if not self.ping():
            return []
        assert self._engine is not None
        sql = text(
            """
            SELECT s.session_id, s.title, s.message_count, s.created_at, s.updated_at,
                   COALESCE(NULLIF(s.title, ''),
                            (SELECT LEFT(m.content, 24) FROM chat_messages m
                             WHERE m.session_id = s.session_id AND m.role = 'user'
                             ORDER BY m.seq LIMIT 1),
                            '新对话') AS display_title
            FROM chat_sessions s
            WHERE s.user_id = :uid AND s.status = 'active'
            ORDER BY s.updated_at DESC
            LIMIT :limit
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"uid": user_id, "limit": limit}).mappings().all()
        return [dict(r) for r in rows]

    def session_owner(self, session_id: str) -> str | None:
        """查询会话归属 user_id（越权校验用）；不存在返回 None。"""
        if not self.ping():
            return None
        assert self._engine is not None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT user_id FROM chat_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).mappings().first()
        return str(row["user_id"]) if row else None

    def rename_session(self, session_id: str, user_id: str, title: str) -> bool:
        """重命名会话（归属校验）；成功返回 True。"""
        if not self.ping():
            return False
        assert self._engine is not None
        with self._engine.begin() as conn:
            result = conn.execute(
                text("UPDATE chat_sessions SET title = :title WHERE session_id = :sid AND user_id = :uid"),
                {"title": title[:255], "sid": session_id, "uid": user_id},
            )
        return (result.rowcount or 0) > 0

    def close_session(self, session_id: str, user_id: str) -> bool:
        """软删会话（status='closed'，保留记忆提取水位）；成功返回 True。"""
        if not self.ping():
            return False
        assert self._engine is not None
        with self._engine.begin() as conn:
            result = conn.execute(
                text("UPDATE chat_sessions SET status = 'closed' WHERE session_id = :sid AND user_id = :uid"),
                {"sid": session_id, "uid": user_id},
            )
        return (result.rowcount or 0) > 0

    def count_unextracted(self, session_id: str) -> int:
        """未提取消息数（message_count - last_extracted_seq）。"""
        if not self.ping():
            return 0
        assert self._engine is not None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT message_count, last_extracted_seq FROM chat_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).mappings().first()
        if not row:
            return 0
        return max(0, int(row["message_count"]) - int(row["last_extracted_seq"]))

    def get_extract_state(self, session_id: str) -> dict:
        """提取水位 + 上次失败时间戳（退避判断）。"""
        if not self.ping():
            return {"last_extracted_seq": 0, "last_failure_at": 0}
        assert self._engine is not None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT message_count, last_extracted_seq, last_failure_at FROM chat_sessions WHERE session_id = :sid"),
                {"sid": session_id},
            ).mappings().first()
        if not row:
            return {"last_extracted_seq": 0, "last_failure_at": 0}
        return {"last_extracted_seq": int(row["last_extracted_seq"]), "last_failure_at": int(row["last_failure_at"] or 0)}

    def update_extracted_seq(self, session_id: str, seq: int) -> None:
        """提取水位推进（全部 upsert 成功后才调用，幂等）。"""
        if not self.ping():
            return
        assert self._engine is not None
        with self._engine.begin() as conn:
            conn.execute(
                text("UPDATE chat_sessions SET last_extracted_seq = :seq WHERE session_id = :sid AND last_extracted_seq < :seq"),
                {"seq": seq, "sid": session_id},
            )

    def mark_extract_failure(self, session_id: str) -> None:
        """记录提取失败时间戳（退避用）。"""
        if not self.ping():
            return
        assert self._engine is not None
        import time

        with self._engine.begin() as conn:
            conn.execute(
                text("UPDATE chat_sessions SET last_failure_at = :t WHERE session_id = :sid"),
                {"t": int(time.time()), "sid": session_id},
            )

    # ── chat_memory_entries ───────────────────────────────────────────
    def upsert_memory_entry(
        self,
        user_id: str,
        kind: str,
        content: str,
        source_session_id: str = "",
        agent_name: str = "main_agent",
        expires_at=None,
    ) -> None:
        """记忆条目 upsert：NFKC 归一 + md5 唯一键精确去重（Phase 4 D6：按 agent_name 隔离）。

        expires_at: 记忆过期时间（naive UTC）。再次提取命中同一条（content_hash 相同）时刷新续期。
        """
        if not self.ping():
            return
        assert self._engine is not None
        norm = unicodedata.normalize("NFKC", content).strip()
        if not norm:
            return
        digest = hashlib.md5(norm.encode("utf-8")).hexdigest()
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO chat_memory_entries (user_id, agent_name, kind, content, content_hash, source_session_id, expires_at) "
                    "VALUES (:uid, :agent, :kind, :content, :hash, :src, :exp) "
                    "ON DUPLICATE KEY UPDATE content = VALUES(content), source_session_id = VALUES(source_session_id), "
                    "expires_at = VALUES(expires_at)"
                ),
                {
                    "uid": user_id,
                    "agent": agent_name,
                    "kind": kind,
                    "content": norm,
                    "hash": digest,
                    "src": source_session_id,
                    "exp": expires_at,
                },
            )

    def list_memory_entries(
        self,
        user_id: str,
        limit: int = 50,
        max_chars: int = 2000,
        agent_name: str = "main_agent",
        include_expired: bool = False,
    ) -> list[dict]:
        """列出记忆条目（默认隐藏已过期；include_expired=True 供合并/清理使用）。"""
        if not self.ping():
            return []
        assert self._engine is not None
        where = "WHERE user_id = :uid AND agent_name = :agent"
        params: dict = {"uid": user_id, "agent": agent_name, "limit": limit}
        if not include_expired:
            where += " AND (expires_at IS NULL OR expires_at > :cutoff)"
            params["cutoff"] = _utcnow_naive()
        sql = text(
            "SELECT kind, content, source_session_id, updated_at FROM chat_memory_entries "
            + where
            + " ORDER BY updated_at DESC LIMIT :limit"
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        entries = [dict(r) for r in rows]
        # 总字符上限（注入容量保护）
        total = 0
        trimmed: list[dict] = []
        for e in entries:
            if total >= max_chars:
                break
            trimmed.append(e)
            total += len(str(e["content"]))
        return trimmed

    def delete_expired(self, user_id: str, agent_name: str = "main_agent") -> int:
        """物理清理已过期记忆条目（consolidation 顺带调用）。返回删除行数。"""
        if not self.ping():
            return 0
        assert self._engine is not None
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "DELETE FROM chat_memory_entries "
                    "WHERE user_id = :uid AND agent_name = :agent AND expires_at IS NOT NULL AND expires_at <= :cutoff"
                ),
                {"uid": user_id, "agent": agent_name, "cutoff": _utcnow_naive()},
            )
        return int(result.rowcount or 0)

    def delete_memory_entries(self, user_id: str, contents: list[str], agent_name: str = "main_agent") -> None:
        """按内容精确删除记忆条目（consolidation 替换旧条目用；按 agent_name 隔离）。"""
        if not self.ping() or not contents:
            return
        assert self._engine is not None
        norms = [unicodedata.normalize("NFKC", str(c)).strip() for c in contents]
        sql = text(
            "DELETE FROM chat_memory_entries WHERE user_id = :uid AND agent_name = :agent AND content IN :contents"
        ).bindparams(bindparam("contents", expanding=True))
        with self._engine.begin() as conn:
            conn.execute(sql, {"uid": user_id, "agent": agent_name, "contents": norms})

    def replace_memory_entries(
        self,
        user_id: str,
        delete_contents: list[str],
        upsert_entries: list[tuple[str, str]],
        agent_name: str = "main_agent",
        upsert_expires=None,
    ) -> None:
        """原子替换记忆条目：单事务内 DELETE 旧内容 + INSERT/UPDATE 新条目（按 agent_name 隔离）。

        Args:
            delete_contents: 要删除的旧条目内容列表
            upsert_entries: (kind, content) 新条目列表
            upsert_expires: 与 upsert_entries 等长的过期时间列表（可为 None → 全不设过期）
        """
        if not self.ping():
            return
        assert self._engine is not None
        delete_sql = text(
            "DELETE FROM chat_memory_entries WHERE user_id = :uid AND agent_name = :agent AND content IN :contents"
        ).bindparams(bindparam("contents", expanding=True))
        upsert_sql = text(
            "INSERT INTO chat_memory_entries (user_id, agent_name, kind, content, content_hash, source_session_id, expires_at) "
            "VALUES (:uid, :agent, :kind, :content, :hash, :src, :exp) "
            "ON DUPLICATE KEY UPDATE content = VALUES(content), source_session_id = VALUES(source_session_id), "
            "expires_at = VALUES(expires_at)"
        )
        with self._engine.begin() as conn:
            if delete_contents:
                conn.execute(
                    delete_sql,
                    {
                        "uid": user_id,
                        "agent": agent_name,
                        "contents": [unicodedata.normalize("NFKC", str(c)).strip() for c in delete_contents],
                    },
                )
            for i, (kind, content) in enumerate(upsert_entries):
                norm = unicodedata.normalize("NFKC", str(content)).strip()
                if not norm:
                    continue
                exp = upsert_expires[i] if upsert_expires is not None and i < len(upsert_expires) else None
                conn.execute(
                    upsert_sql,
                    {
                        "uid": user_id,
                        "agent": agent_name,
                        "kind": kind,
                        "content": norm,
                        "hash": hashlib.md5(norm.encode("utf-8")).hexdigest(),
                        "src": "consolidate",
                        "exp": exp,
                    },
                )


    # ── chat_session_compactions（Phase 4 P0-A） ─────────────────────
    def append_compaction(
        self,
        *,
        user_id: str,
        session_id: str,
        summary: str,
        prev_compaction_id: int | None = None,
        first_kept_message_id: int = 0,
        tokens_before: int = 0,
        tokens_after: int = 0,
        reserve_tokens: int = 0,
        keep_recent_tokens: int = 0,
        model: str = "",
        reason: str = "threshold",
        status: str = "ok",
        usage_json: str | None = None,
        details_json: str | None = None,
    ) -> int:
        """落一条压缩记录（写后同步）；返回新行 id。幂等由调用方（middleware 防抖）保证。"""
        if not self.ping():
            return 0
        assert self._engine is not None
        with self._engine.begin() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO chat_session_compactions "
                    "(user_id, session_id, summary, prev_compaction_id, first_kept_message_id, "
                    " tokens_before, tokens_after, reserve_tokens, keep_recent_tokens, model, "
                    " reason, status, usage_json, details_json) "
                    "VALUES (:uid, :sid, :summary, :prev, :first_kept, :tb, :ta, :reserve, :keep, :model, "
                    " :reason, :status, :usage, :details)"
                ),
                {
                    "uid": user_id,
                    "sid": session_id,
                    "summary": summary,
                    "prev": prev_compaction_id,
                    "first_kept": int(first_kept_message_id or 0),
                    "tb": int(tokens_before or 0),
                    "ta": int(tokens_after or 0),
                    "reserve": int(reserve_tokens or 0),
                    "keep": int(keep_recent_tokens or 0),
                    "model": model,
                    "reason": reason,
                    "status": status,
                    "usage": usage_json,
                    "details": details_json,
                },
            )
        return int(result.lastrowid or 0)

    def get_latest_compaction(self, session_id: str) -> dict | None:
        """最新一条压缩记录（读路径注入用）；无则 None。"""
        if not self.ping():
            return None
        assert self._engine is not None
        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT id, user_id, session_id, summary, prev_compaction_id, first_kept_message_id, "
                    " tokens_before, tokens_after, reason, status, created_at "
                    "FROM chat_session_compactions WHERE session_id = :sid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"sid": session_id},
            ).mappings().first()
        return dict(row) if row else None

    def list_compactions(self, session_id: str, limit: int = 50) -> list[dict]:
        """按时间倒序列压缩记录（审计/详情）。"""
        if not self.ping():
            return []
        assert self._engine is not None
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, user_id, session_id, summary, prev_compaction_id, first_kept_message_id, "
                    " tokens_before, tokens_after, reason, status, created_at "
                    "FROM chat_session_compactions WHERE session_id = :sid "
                    "ORDER BY id DESC LIMIT :limit"
                ),
                {"sid": session_id, "limit": limit},
            ).mappings().all()
        return [dict(r) for r in rows]

    def list_entries_after_seq(self, session_id: str, seq: int, limit: int = 500) -> list[dict]:
        """续读：seq 之后的消息（compaction 读路径 / 审计用）。"""
        return self.list_messages(session_id, after_seq=seq, limit=limit)
