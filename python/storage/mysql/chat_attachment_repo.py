# -*- coding: utf-8 -*-
"""chat_attachments 元数据仓储。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .base import MySQLRepository


class ChatAttachmentRepository(MySQLRepository):
    """私有聊天图片资产 CRUD；表结构由 sql/init-db.sql 维护。"""

    def create(self, asset: dict[str, Any]) -> bool:
        if not self.ping():
            return False
        assert self._engine is not None
        sql = text(
            """
            INSERT INTO chat_attachments (
                attachment_id, user_id, session_id, file_key, mime_type, filename,
                file_size, width, height, sha256, status, expires_at
            ) VALUES (
                :attachment_id, :user_id, :session_id, :file_key, :mime_type, :filename,
                :file_size, :width, :height, :sha256, :status, :expires_at
            )
            ON DUPLICATE KEY UPDATE
                file_key = VALUES(file_key),
                mime_type = VALUES(mime_type),
                filename = VALUES(filename),
                file_size = VALUES(file_size),
                width = VALUES(width),
                height = VALUES(height),
                sha256 = VALUES(sha256),
                status = VALUES(status),
                expires_at = VALUES(expires_at)
            """
        )
        params = {
            "attachment_id": asset["image_id"],
            "user_id": asset["user_id"],
            "session_id": asset["session_id"],
            "file_key": asset["file_key"],
            "mime_type": asset["mime_type"],
            "filename": asset.get("filename", "")[:255],
            "file_size": int(asset.get("file_size", 0)),
            "width": int(asset.get("width", 0)),
            "height": int(asset.get("height", 0)),
            "sha256": asset.get("sha256", ""),
            "status": asset.get("status", "ready"),
            "expires_at": asset.get("expires_at"),
        }
        with self._engine.begin() as conn:
            conn.execute(sql, params)
        return True

    def get(self, image_id: str) -> dict[str, Any] | None:
        if not self.ping():
            return None
        assert self._engine is not None
        sql = text(
            """
            SELECT attachment_id, user_id, session_id, file_key, mime_type, filename,
                   file_size, width, height, sha256, status, created_at, expires_at
            FROM chat_attachments
            WHERE attachment_id = :image_id
            """
        )
        with self._engine.connect() as conn:
            row = conn.execute(sql, {"image_id": image_id}).mappings().first()
        if not row:
            return None
        return {
            "image_id": str(row["attachment_id"]),
            "user_id": str(row["user_id"]),
            "session_id": str(row["session_id"]),
            "file_key": str(row["file_key"]),
            "mime_type": str(row["mime_type"]),
            "filename": str(row["filename"] or ""),
            "file_size": int(row["file_size"] or 0),
            "width": int(row["width"] or 0),
            "height": int(row["height"] or 0),
            "sha256": str(row["sha256"] or ""),
            "status": str(row["status"] or "ready"),
            "created_at": str(row["created_at"] or ""),
            "expires_at": str(row["expires_at"] or ""),
        }

    def delete(self, image_id: str) -> bool:
        if not self.ping():
            return False
        assert self._engine is not None
        sql = text("UPDATE chat_attachments SET status = 'deleted' WHERE attachment_id = :image_id")
        with self._engine.begin() as conn:
            result = conn.execute(sql, {"image_id": image_id})
        return bool(result.rowcount)