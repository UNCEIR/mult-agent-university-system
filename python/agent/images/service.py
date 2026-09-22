# -*- coding: utf-8 -*-
"""聊天图片资产服务：格式归一化、私有存储、owner 校验与本地兜底。

设计边界：
- API / Chat 层只拿 ``image_id``，不向 Agent 暴露本地路径、base64 或任意外部 URL。
- MinIO 可用时优先对象存储；不可用时由 MinioRepository 自动落本地磁盘。
- MySQL 元数据不可用时，始终写本地 JSON sidecar，保证单机/开发环境仍可读取。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError
import structlog

logger = structlog.get_logger()

MAX_FILES = 4
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 30 * 1024 * 1024
MAX_PIXELS = 36_000_000
MAX_SIDE = 2048
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "BMP"}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_IMAGE_ID_RE = re.compile(r"^img_[A-Za-z0-9]{16,32}$")


class ImageAssetError(Exception):
    """结构化图片资产错误。"""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def to_payload(self) -> dict[str, Any]:
        return {"isError": True, "code": self.code, "message": self.message, "retryable": False}


@dataclass(frozen=True)
class NormalizedImage:
    data: bytes
    mime_type: str
    extension: str
    width: int
    height: int
    sha256: str


class ImageAssetService:
    """图片资产主服务（所有路径都通过服务接口访问）。"""

    def __init__(
        self,
        *,
        metadata_repo: Any | None = None,
        minio_repo: Any | None = None,
        fallback_root: str | Path | None = None,
        ttl_days: int = 30,
        max_images: int = MAX_FILES,
        max_image_bytes: int = MAX_FILE_BYTES,
        max_total_bytes: int = MAX_TOTAL_BYTES,
        max_pixels: int = MAX_PIXELS,
        max_side: int = MAX_SIDE,
    ) -> None:
        self.metadata_repo = metadata_repo
        self.minio_repo = minio_repo
        self.ttl_days = max(1, int(ttl_days))
        self.max_images = max(1, int(max_images))
        self.max_image_bytes = max(1, int(max_image_bytes))
        self.max_total_bytes = max(self.max_image_bytes, int(max_total_bytes))
        self.max_pixels = max(1, int(max_pixels))
        self.max_side = max(1, int(max_side))
        self.fallback_root = Path(fallback_root) if fallback_root else (
            Path(__file__).resolve().parents[2] / ".documents" / "chat_images"
        )
        self.objects_root = self.fallback_root / "objects"
        self.metadata_root = self.fallback_root / "metadata"
        self.objects_root.mkdir(parents=True, exist_ok=True)
        self.metadata_root.mkdir(parents=True, exist_ok=True)

    # ── 上传 ────────────────────────────────────────────────────────
    async def ingest_many(
        self,
        files: list[Any],
        *,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """批量摄入图片；文件名/后缀不可信，按真实图片内容解码。"""
        if not _SAFE_ID_RE.fullmatch(user_id or ""):
            raise ImageAssetError("IMAGE_FORBIDDEN", "用户身份无效", status_code=401)
        if not _SAFE_ID_RE.fullmatch(session_id or ""):
            raise ImageAssetError("INVALID_SESSION_ID", "会话 ID 格式不合法", status_code=400)
        if not files:
            raise ImageAssetError("IMAGE_REQUIRED", "请至少上传一张图片", status_code=400)
        if len(files) > self.max_images:
            raise ImageAssetError("IMAGE_TOO_MANY", f"单次最多上传 {self.max_images} 张图片", status_code=400)

        assets: list[dict[str, Any]] = []
        total_size = 0
        for file in files:
            data = await file.read(self.max_image_bytes + 1)
            if len(data) > self.max_image_bytes:
                raise ImageAssetError("IMAGE_TOO_LARGE", "单张图片不能超过 10MB", status_code=413)
            total_size += len(data)
            if total_size > self.max_total_bytes:
                raise ImageAssetError("IMAGE_TOO_LARGE", "图片总大小不能超过 30MB", status_code=413)
            normalized = await asyncio.to_thread(_normalize_image, data, self.max_pixels, self.max_side)
            image_id = f"img_{uuid.uuid4().hex[:24]}"
            key = self._object_key(user_id=user_id, session_id=session_id, image_id=image_id, ext=normalized.extension)
            await self._store_object(key, normalized.data, content_type=normalized.mime_type)
            now = datetime.now(timezone.utc)
            metadata = {
                "image_id": image_id,
                "user_id": user_id,
                "session_id": session_id,
                "file_key": key,
                "mime_type": normalized.mime_type,
                "file_size": len(normalized.data),
                "width": normalized.width,
                "height": normalized.height,
                "sha256": normalized.sha256,
                "filename": Path(getattr(file, "filename", "") or "image").name[:255],
                "status": "ready",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(days=self.ttl_days)).isoformat(),
            }
            metadata["preview_url"] = make_preview_url(image_id, user_id, session_id)
            await self._save_metadata(metadata)
            assets.append(metadata)
            logger.info(
                "chat_image.uploaded",
                image_id=image_id,
                user_id_hash=_hash_text(user_id)[:12],
                session_id=session_id,
                mime_type=normalized.mime_type,
                file_size=len(normalized.data),
                storage_backend="minio_or_local",
            )
        return assets

    async def ingest_data_items(
        self,
        items: list[str],
        *,
        user_id: str,
        session_id: str,
    ) -> list[dict[str, Any]]:
        """兼容旧 ``ChatRequest.images`` 的 data URL 入口。

        只接受 data:image/...;base64,...；不再服务端下载任意外部 URL，避免 SSRF。
        """
        uploads: list[Any] = []
        for raw in items[:MAX_FILES]:
            if not isinstance(raw, str) or not raw.startswith("data:image/"):
                continue
            try:
                header, payload = raw.split(",", 1)
                if ";base64" not in header:
                    continue
                data = base64.b64decode(payload, validate=True)
            except Exception:  # noqa: BLE001
                continue
            uploads.append(_BytesUpload(filename="legacy-data-url", data=data, content_type="image/*"))
        return await self.ingest_many(uploads, user_id=user_id, session_id=session_id)

    async def resolve_many(
        self,
        image_ids: list[str],
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按 owner 校验并解析图片元数据，保持调用方顺序。"""
        if not image_ids:
            return []
        if len(image_ids) > self.max_images:
            raise ImageAssetError("IMAGE_TOO_MANY", f"单次最多分析 {self.max_images} 张图片", status_code=400)
        resolved: list[dict[str, Any]] = []
        for image_id in image_ids:
            if not _IMAGE_ID_RE.fullmatch(image_id or ""):
                raise ImageAssetError("IMAGE_NOT_FOUND", "图片资产不存在", status_code=404)
            metadata = await self._load_metadata(image_id)
            if not metadata or metadata.get("status") != "ready":
                raise ImageAssetError("IMAGE_NOT_FOUND", "图片资产不存在或已过期", status_code=404)
            if str(metadata.get("user_id", "")) != user_id:
                raise ImageAssetError("IMAGE_FORBIDDEN", "无权访问该图片", status_code=403)
            if session_id is not None and str(metadata.get("session_id", "")) != session_id:
                raise ImageAssetError("IMAGE_FORBIDDEN", "图片不属于当前会话", status_code=403)
            if _is_expired(metadata.get("expires_at")):
                raise ImageAssetError("IMAGE_NOT_FOUND", "图片资产已过期", status_code=404)
            resolved.append(metadata)
        return resolved

    async def load(self, image_id: str, *, user_id: str, session_id: str | None = None) -> tuple[bytes, str]:
        """读取私有图片字节；调用方必须先通过 resolve/owner 校验。"""
        metadata = (await self.resolve_many([image_id], user_id=user_id, session_id=session_id))[0]
        data = await self._load_object(str(metadata["file_key"]))
        if data is None:
            raise ImageAssetError("IMAGE_NOT_FOUND", "图片文件不存在或已被清理", status_code=404)
        return data, str(metadata.get("mime_type") or "application/octet-stream")

    async def delete(self, image_id: str, *, user_id: str, session_id: str | None = None) -> bool:
        """删除图片资产（元数据 + 对象存储，双后端尽力删除）。"""
        metadata = await self._load_metadata(image_id)
        if not metadata:
            return False
        if str(metadata.get("user_id", "")) != user_id:
            raise ImageAssetError("IMAGE_FORBIDDEN", "无权删除该图片", status_code=403)
        if session_id is not None and str(metadata.get("session_id", "")) != session_id:
            raise ImageAssetError("IMAGE_FORBIDDEN", "图片不属于当前会话", status_code=403)
        if _is_expired(metadata.get("expires_at")):
            return False
        metadata["status"] = "deleted"
        await self._save_metadata(metadata)
        await self._delete_object(str(metadata.get("file_key", "")))
        return True

    # ── 存储兜底 ────────────────────────────────────────────────────
    def _object_key(self, *, user_id: str, session_id: str, image_id: str, ext: str) -> str:
        user_hash = _hash_text(user_id)[:16]
        return f"chat-uploads/{user_hash}/{session_id}/{image_id}.{ext}"

    async def _store_object(self, key: str, data: bytes, *, content_type: str) -> None:
        if self.minio_repo is not None:
            try:
                await asyncio.to_thread(
                    self.minio_repo.upload,
                    key,
                    data,
                    content_type=content_type,
                )
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat_image.object_store_failed", key=key, error=str(exc)[:120])
        path = self.objects_root / key
        await asyncio.to_thread(_write_bytes, path, data)

    async def _load_object(self, key: str) -> bytes | None:
        if self.minio_repo is not None:
            try:
                data = await asyncio.to_thread(self.minio_repo.download, key)
                if data is not None:
                    return data
            except Exception:  # noqa: BLE001
                pass
        path = self.objects_root / key
        if path.is_file():
            return await asyncio.to_thread(path.read_bytes)
        return None

    async def _delete_object(self, key: str) -> None:
        if not key:
            return
        if self.minio_repo is not None:
            try:
                await asyncio.to_thread(self.minio_repo.delete, key)
            except Exception:  # noqa: BLE001
                pass
        path = self.objects_root / key
        if path.is_file():
            await asyncio.to_thread(path.unlink, True)

    async def _save_metadata(self, metadata: dict[str, Any]) -> None:
        # 本地 sidecar 始终先写：MySQL 不可用 / 对象存储冷却时仍可恢复单机资产。
        path = self.metadata_root / f"{metadata['image_id']}.json"
        await asyncio.to_thread(
            _write_text,
            path,
            json.dumps(metadata, ensure_ascii=False, indent=2),
        )
        if self.metadata_repo is not None:
            try:
                await asyncio.to_thread(self.metadata_repo.create, metadata)
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat_image.metadata_db_failed", image_id=metadata.get("image_id"), error=str(exc)[:120])

    async def _load_sidecar_metadata(self, image_id: str) -> dict[str, Any] | None:
        path = self.metadata_root / f"{image_id}.json"
        if not path.is_file():
            return None
        try:
            raw = await asyncio.to_thread(path.read_text, "utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None

    async def _load_metadata(self, image_id: str) -> dict[str, Any] | None:
        sidecar = await self._load_sidecar_metadata(image_id)
        row: dict[str, Any] | None = None
        if self.metadata_repo is not None:
            try:
                loaded = await asyncio.to_thread(self.metadata_repo.get, image_id)
                if loaded:
                    row = dict(loaded)
            except Exception:  # noqa: BLE001
                pass
        if not row:
            return sidecar
        merged = dict(sidecar or {})
        merged.update({key: value for key, value in row.items() if value not in (None, "")})
        merged.setdefault("filename", (sidecar or {}).get("filename", ""))
        merged["preview_url"] = make_preview_url(
            str(merged["image_id"]),
            str(merged["user_id"]),
            str(merged.get("session_id", "")),
        )
        return merged

class _BytesUpload:
    """把 data URL 兼容项包装成与 UploadFile 等价的最小接口。"""

    def __init__(self, *, filename: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.filename = filename
        self.content_type = content_type
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._data
        return self._data[:size]


def _normalize_image(data: bytes, max_pixels: int = MAX_PIXELS, max_side: int = MAX_SIDE) -> NormalizedImage:
    """把主流图片归一化为 VLM 友好的 PNG/JPEG。"""
    if not data:
        raise ImageAssetError("IMAGE_DECODE_FAILED", "图片内容为空")
    Image.MAX_IMAGE_PIXELS = max_pixels
    try:
        with Image.open(BytesIO(data)) as probe:
            source_format = str(probe.format or "").upper()
            if source_format not in ALLOWED_FORMATS:
                raise ImageAssetError("IMAGE_BAD_TYPE", "仅支持 PNG/JPEG/WebP/GIF/BMP 图片")
            probe.verify()
        with Image.open(BytesIO(data)) as source:
            source.seek(0)
            image = ImageOps.exif_transpose(source)
            image.load()
            width, height = image.size
            if width < 1 or height < 1 or width * height > max_pixels:
                raise ImageAssetError("IMAGE_TOO_LARGE", "图片像素尺寸过大")
            if max(width, height) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            out = BytesIO()
            if source_format == "JPEG":
                rgb = image.convert("RGB") if image.mode != "RGB" else image
                rgb.save(out, format="JPEG", quality=90, optimize=True)
                mime_type, extension = "image/jpeg", "jpg"
            else:
                rgba = image.convert("RGBA") if image.mode not in ("RGBA", "RGB") else image
                if rgba.mode == "RGB":
                    rgba = rgba.convert("RGBA")
                rgba.save(out, format="PNG", optimize=True)
                mime_type, extension = "image/png", "png"
            normalized = out.getvalue()
            final_width, final_height = image.size
            return NormalizedImage(
                data=normalized,
                mime_type=mime_type,
                extension=extension,
                width=final_width,
                height=final_height,
                sha256=hashlib.sha256(normalized).hexdigest(),
            )
    except ImageAssetError:
        raise
    except Image.DecompressionBombError as exc:
        raise ImageAssetError("IMAGE_TOO_LARGE", "图片像素尺寸过大", status_code=413) from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageAssetError("IMAGE_DECODE_FAILED", "图片无法解码，请更换文件后重试") from exc


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _is_expired(value: Any) -> bool:
    if not value:
        return False
    try:
        expires = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return False

def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_service = None


def configure_image_asset_service(
    *,
    metadata_repo=None,
    minio_repo=None,
    fallback_root=None,
    ttl_days: int = 30,
):
    """由 runtime/测试注入单例；重复调用会替换旧实例。"""
    global _service
    _service = ImageAssetService(
        metadata_repo=metadata_repo,
        minio_repo=minio_repo,
        fallback_root=fallback_root,
        ttl_days=ttl_days,
    )
    return _service


def get_image_asset_service():
    """懒加载生产单例，避免 import 时强制连接 MinIO/MySQL。"""
    global _service
    if _service is None:
        from config import get_settings
        from storage.minio.minio_repo import MinioRepository
        from storage.mysql.chat_attachment_repo import ChatAttachmentRepository

        settings = get_settings()
        root = Path(__file__).resolve().parents[2] / ".documents" / "chat_images"
        minio = MinioRepository(
            endpoint=settings.minio_endpoint,
            port=settings.minio_port,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            bucket=settings.minio_chat_bucket,
            connect_timeout=settings.minio_connect_timeout,
            local_root=root / "objects",
        )
        _service = ImageAssetService(
            metadata_repo=ChatAttachmentRepository(),
            minio_repo=minio,
            fallback_root=root,
            ttl_days=settings.vision_ttl_days,
            max_images=settings.vision_max_images,
            max_image_bytes=settings.vision_max_image_bytes,
            max_total_bytes=settings.vision_max_total_image_bytes,
            max_pixels=settings.vision_max_pixels,
            max_side=settings.vision_max_side,
        )
    return _service

def make_preview_url(image_id: str, user_id: str, session_id: str = "") -> str:
    """私有图片预览地址；前端必须通过当前用户身份访问。"""
    from urllib.parse import urlencode

    params = {"user_id": user_id}
    if session_id:
        params["session_id"] = session_id
    return f"/api/v1/chat/images/{image_id}/content?{urlencode(params)}"