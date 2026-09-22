"""文档元数据仓储 — MySQL document_records / document_chunks 表。

表结构由 sql/init-db.sql 定义，本仓储只做 CRUD，不建表。
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog
from sqlalchemy import text

from .base import MySQLRepository

logger = structlog.get_logger()


def _lexical_tokens(query: str) -> list[str]:
    """把中文长查询拆成 2-gram/关键词，用于 FTS 无命中时的 LIKE 兜底。"""
    normalized = re.sub(r"\s+", " ", query.strip())
    if not normalized:
        return []
    tokens: list[str] = []
    for part in re.split(r"[\s,，。；;:：/\\|()（）\[\]【】]+", normalized):
        if len(part) >= 2:
            tokens.append(part[:24])
    compact = "".join(ch for ch in normalized if ch.isalnum())
    if len(compact) >= 2:
        tokens.extend(compact[idx : idx + 2] for idx in range(0, min(len(compact) - 1, 10)))
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token not in seen:
            seen.add(token)
            out.append(token)
        if len(out) >= 8:
            break
    return out



class DocumentRepository(MySQLRepository):
    """document_records / document_chunks 元数据 CRUD。"""

    def create_dataset(
        self,
        *,
        dataset_id: str,
        dataset_name: str,
        source_doc_name: str,
        storage_path: str,
        file_type: str,
        file_size: int = 0,
        chunk_strategy: str = "auto",
        chunks_count: int = 0,
        status: str = "pending",
        user_id: str = "public",
    ) -> None:
        if not self.ping():
            raise RuntimeError("MySQL is not available")
        assert self._engine is not None
        sql = text(
            """
            INSERT INTO document_records (
                dataset_id, dataset_name, source_doc_name, storage_path,
                file_type, file_size, chunk_strategy, chunks_count, status,
                user_id
            ) VALUES (
                :dataset_id, :dataset_name, :source_doc_name, :storage_path,
                :file_type, :file_size, :chunk_strategy, :chunks_count, :status,
                :user_id
            )
            ON DUPLICATE KEY UPDATE
                dataset_name = VALUES(dataset_name),
                storage_path = VALUES(storage_path),
                file_size = VALUES(file_size),
                chunk_strategy = VALUES(chunk_strategy),
                chunks_count = VALUES(chunks_count),
                status = VALUES(status),
                user_id = VALUES(user_id)
            """
        )
        with self._engine.begin() as conn:
            conn.execute(
                sql,
                {
                    "dataset_id": dataset_id,
                    "dataset_name": dataset_name,
                    "source_doc_name": source_doc_name,
                    "storage_path": storage_path,
                    "file_type": file_type,
                    "file_size": file_size,
                    "chunk_strategy": chunk_strategy,
                    "chunks_count": chunks_count,
                    "status": status,
                    "user_id": user_id,
                },
            )

    def set_dataset_status(self, dataset_id: str, status: str, error_message: str = "") -> None:
        if not self.ping():
            return
        assert self._engine is not None
        sql = text(
            "UPDATE document_records SET status = :status, error_message = :error_message "
            "WHERE dataset_id = :dataset_id"
        )
        with self._engine.begin() as conn:
            conn.execute(
                sql,
                {"status": status, "error_message": error_message, "dataset_id": dataset_id},
            )

    def replace_chunks(self, dataset_id: str, chunks: list[dict[str, Any]]) -> None:
        """以 dataset_id 为粒度整体替换 chunk 元数据（增量更新去旧）。"""
        if not self.ping():
            raise RuntimeError("MySQL is not available")
        assert self._engine is not None
        delete_sql = text("DELETE FROM document_chunks WHERE dataset_id = :dataset_id")
        insert_sql = text(
            """
            INSERT INTO document_chunks (
                chunk_id, dataset_id, chunk_index, chunk_type, content_preview,
                page_number, milvus_vector_id, content, metadata_json
            ) VALUES (
                :chunk_id, :dataset_id, :chunk_index, :chunk_type, :content_preview,
                :page_number, :milvus_vector_id, :content, :metadata_json
            )
            """
        )
        with self._engine.begin() as conn:
            conn.execute(delete_sql, {"dataset_id": dataset_id})
            if chunks:
                rows = [
                    {
                        "chunk_id": c["chunk_id"],
                        "dataset_id": dataset_id,
                        "chunk_index": c.get("chunk_index", 0),
                        "chunk_type": c.get("chunk_type", "generic_fixed"),
                        "content_preview": c.get("content", "")[:512],
                        "page_number": int(c.get("page_number", 0)),
                        "milvus_vector_id": c.get("chunk_id", ""),
                        "content": c.get("content", ""),
                        "metadata_json": json.dumps(
                            c.get("metadata", {}), ensure_ascii=False
                        ),
                    }
                    for c in chunks
                ]
                conn.execute(insert_sql, rows)

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        if not self.ping():
            return None
        assert self._engine is not None
        sql = text(
            "SELECT dataset_id, dataset_name, source_doc_name, storage_path, file_type, "
            "file_size, chunk_strategy, chunks_count, status, created_at "
            "FROM document_records WHERE dataset_id = :dataset_id"
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"dataset_id": dataset_id}).mappings().first()
        return dict(row) if row else None

    def list_datasets(
        self,
        *,
        limit: int = 50,
        user_id: str | None = None,
        include_public: bool = True,
    ) -> list[dict[str, Any]]:
        """列出文档数据集。

        Args:
            limit: 最大返回条数
            user_id: 当指定时，过滤 user_id=user_id 的记录；如果 include_public=True，
                同时返回 user_id='public' 的（手册类）。
            include_public: 是否包含公共手册；与 user_id 一起过滤。
                include_public=False 时只看 user_id 严格等于 user_id 的（个人数据集列表）。
        """
        if not self.ping():
            return []
        assert self._engine is not None
        if user_id is None:
            # 老语义：列出全部（兼容上层无 user_id 的调用）
            sql = text(
                "SELECT dataset_id, dataset_name, source_doc_name, file_type, "
                "chunks_count, status, user_id "
                "FROM document_records ORDER BY created_at DESC LIMIT :limit"
            )
            params: dict[str, Any] = {"limit": limit}
        else:
            if include_public:
                sql = text(
                    "SELECT dataset_id, dataset_name, source_doc_name, file_type, "
                    "chunks_count, status, user_id "
                    "FROM document_records WHERE user_id IN ('public', :user_id) "
                    "ORDER BY created_at DESC LIMIT :limit"
                )
            else:
                sql = text(
                    "SELECT dataset_id, dataset_name, source_doc_name, file_type, "
                    "chunks_count, status, user_id "
                    "FROM document_records WHERE user_id = :user_id "
                    "ORDER BY created_at DESC LIMIT :limit"
                )
            params = {"user_id": user_id, "limit": limit}
        with self._engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [dict(row) for row in rows]

    def search_lexical(
        self,
        query: str,
        user_ids: list[str],
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """MySQL ngram FULLTEXT lexical 召回；FTS 无命中/不可用时退化为 token LIKE。"""
        if not query.strip() or not user_ids or not self.ping():
            return []
        assert self._engine is not None
        allowed = ", ".join(f":u_{idx}" for idx in range(len(user_ids)))
        limit = max(1, min(int(top_k), 50))
        base_params: dict[str, Any] = {"top_k": limit}
        base_params.update({f"u_{idx}": uid for idx, uid in enumerate(user_ids)})
        rows = []
        fts_params = dict(base_params)
        fts_params["query"] = query.strip()[:200]
        fts_sql = text(
            f"""SELECT c.chunk_id, c.dataset_id, r.source_doc_name, c.page_number,
                       r.user_id,
                       MATCH(c.content) AGAINST (:query IN NATURAL LANGUAGE MODE) AS lexical_score
                FROM document_chunks c
                JOIN document_records r ON r.dataset_id = c.dataset_id
                WHERE r.user_id IN ({allowed})
                  AND MATCH(c.content) AGAINST (:query IN NATURAL LANGUAGE MODE)
                ORDER BY lexical_score DESC
                LIMIT :top_k"""
        )
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(fts_sql, fts_params).mappings().all()
        except Exception:  # noqa: BLE001
            rows = []

        if not rows:
            tokens = _lexical_tokens(query)
            if tokens:
                like_clauses = []
                like_params = dict(base_params)
                for idx, token in enumerate(tokens):
                    key = f"token_{idx}"
                    like_clauses.append(f"c.content LIKE :{key}")
                    like_params[key] = f"%{token}%"
                like_sql = text(
                    f"""SELECT c.chunk_id, c.dataset_id, r.source_doc_name, c.page_number,
                               r.user_id, 1.0 AS lexical_score
                        FROM document_chunks c
                        JOIN document_records r ON r.dataset_id = c.dataset_id
                        WHERE r.user_id IN ({allowed})
                          AND ({' OR '.join(like_clauses)})
                        ORDER BY c.page_number ASC
                        LIMIT :top_k"""
                )
                with self._engine.connect() as conn:
                    rows = conn.execute(like_sql, like_params).mappings().all()
        return [
            {
                "chunk_id": str(row["chunk_id"]),
                "dataset_id": str(row["dataset_id"] or ""),
                "source_doc_name": str(row["source_doc_name"] or ""),
                "page_number": int(row["page_number"] or 0),
                "section": "",
                "user_id": str(row["user_id"] or ""),
                "lexical_score": float(row["lexical_score"] or 0.0),
            }
            for row in rows
        ]

    def get_chunk_contents(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
        """按 chunk_id 批量取回内容（供 query_knowledge 组装回答上下文）。

        Returns:
            {chunk_id: {content, page_number, source_doc_name}}
        """
        if not chunk_ids or not self.ping():
            return {}
        assert self._engine is not None
        result: dict[str, dict[str, Any]] = {}
        for start in range(0, len(chunk_ids), 200):
            batch = chunk_ids[start : start + 200]
            placeholders = ", ".join(f":c_{idx}" for idx in range(len(batch)))
            params = {f"c_{idx}": value for idx, value in enumerate(batch)}
            sql = text(
                f"SELECT chunk_id, content, page_number, dataset_id FROM document_chunks "
                f"WHERE chunk_id IN ({placeholders})"
            )
            with self._engine.connect() as conn:
                rows = conn.execute(sql, params).mappings().all()
            for row in rows:
                result[str(row["chunk_id"])] = {
                    "content": str(row["content"] or ""),
                    "page_number": int(row["page_number"] or 0),
                    "dataset_id": str(row["dataset_id"] or ""),
                }
        return result

    def get_chunks_by_user(self, user_id: str) -> list[dict[str, Any]]:
        """按 metadata_json.user_id 取该用户全部 chunk（含结构化 courses）。

        Phase 2：evaluation 快照确定性直查源；覆盖 ingest 脚本与
        /documents/upload 两条摄入路径（dataset_name 不可靠，按 JSON 过滤）。
        """
        if not user_id or not self.ping():
            return []
        assert self._engine is not None
        sql = text(
            """
            SELECT chunk_id, dataset_id, chunk_index, content, page_number, metadata_json
            FROM document_chunks
            WHERE JSON_UNQUOTE(JSON_EXTRACT(metadata_json, '$.user_id')) = :user_id
            ORDER BY dataset_id, chunk_index
            """
        )
        with self._engine.connect() as conn:
            rows = conn.execute(sql, {"user_id": user_id}).mappings().all()
        out = []
        for row in rows:
            metadata = row["metadata_json"]
            if isinstance(metadata, str) and metadata:
                try:
                    import json

                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            out.append(
                {
                    "chunk_id": str(row["chunk_id"]),
                    "dataset_id": str(row["dataset_id"] or ""),
                    "content": str(row["content"] or ""),
                    "page_number": int(row["page_number"] or 0),
                    "metadata": metadata or {},
                }
            )
        return out
