# -*- coding: utf-8 -*-
"""chat 图片资产服务：格式规范化、私有存储、owner 校验与本地兜底。"""

from __future__ import annotations

from io import BytesIO

import pytest
from fastapi import UploadFile
from PIL import Image


class _FakeMetadataRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def create(self, asset: dict) -> bool:
        self.rows[asset["image_id"]] = dict(asset)
        return True

    def get(self, image_id: str) -> dict | None:
        row = self.rows.get(image_id)
        return dict(row) if row else None

    def delete(self, image_id: str) -> bool:
        self.rows.pop(image_id, None)
        return True


class _FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def upload(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        self.objects[key] = data
        return key

    def download(self, key: str) -> bytes | None:
        return self.objects.get(key)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


def _png_upload(filename: str = "成绩图.png") -> UploadFile:
    buf = BytesIO()
    Image.new("RGB", (32, 24), "white").save(buf, format="PNG")
    return UploadFile(filename=filename, file=BytesIO(buf.getvalue()), headers={"content-type": "image/png"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_asset_service_accepts_png_and_rejects_cross_user_read(tmp_path):
    from agent.images.service import ImageAssetError, ImageAssetService

    service = ImageAssetService(
        metadata_repo=_FakeMetadataRepo(),
        minio_repo=_FakeObjectStore(),
        fallback_root=tmp_path,
    )
    assets = await service.ingest_many([_png_upload()], user_id="u1", session_id="s1")

    assert len(assets) == 1
    asset = assets[0]
    assert asset["image_id"].startswith("img_")
    assert asset["mime_type"] == "image/png"
    assert asset["width"] == 32 and asset["height"] == 24
    assert asset["preview_url"].startswith("/api/v1/chat/images/")

    data, mime_type = await service.load(asset["image_id"], user_id="u1")
    assert data.startswith(b"\x89PNG")
    assert mime_type == "image/png"

    with pytest.raises(ImageAssetError) as exc:
        await service.load(asset["image_id"], user_id="u2")
    assert exc.value.code == "IMAGE_FORBIDDEN"

class _BrokenMetadataRepo:
    def create(self, asset: dict) -> bool:
        raise RuntimeError("mysql down")

    def get(self, image_id: str) -> dict | None:
        raise RuntimeError("mysql down")

    def delete(self, image_id: str) -> bool:
        raise RuntimeError("mysql down")


class _BrokenObjectStore:
    def upload(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        raise RuntimeError("minio down")

    def download(self, key: str) -> bytes | None:
        raise RuntimeError("minio down")

    def delete(self, key: str) -> None:
        raise RuntimeError("minio down")


def _gif_upload() -> UploadFile:
    buf = BytesIO()
    Image.new("RGBA", (20, 16), (255, 0, 0, 128)).save(buf, format="GIF")
    return UploadFile(filename="notice.gif", file=BytesIO(buf.getvalue()), headers={"content-type": "image/gif"})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_asset_service_converts_gif_and_recovers_with_local_fallback(tmp_path):
    from agent.images.service import ImageAssetService

    service = ImageAssetService(
        metadata_repo=_BrokenMetadataRepo(),
        minio_repo=_BrokenObjectStore(),
        fallback_root=tmp_path,
    )
    assets = await service.ingest_many([_gif_upload()], user_id="u1", session_id="s1")
    image_id = assets[0]["image_id"]
    assert assets[0]["mime_type"] == "image/png"
    assert assets[0]["width"] == 20 and assets[0]["height"] == 16

    recovered = ImageAssetService(metadata_repo=None, minio_repo=None, fallback_root=tmp_path)
    data, mime_type = await recovered.load(image_id, user_id="u1")
    assert data.startswith(b"\x89PNG")
    assert mime_type == "image/png"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_asset_service_rejects_unsupported_bytes(tmp_path):
    from agent.images.service import ImageAssetError, ImageAssetService

    bad = UploadFile(filename="fake.svg", file=BytesIO(b"<svg></svg>"), headers={"content-type": "image/svg+xml"})
    service = ImageAssetService(metadata_repo=_FakeMetadataRepo(), minio_repo=_FakeObjectStore(), fallback_root=tmp_path)
    with pytest.raises(ImageAssetError) as exc:
        await service.ingest_many([bad], user_id="u1", session_id="s1")
    assert exc.value.code == "IMAGE_DECODE_FAILED"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_asset_service_enforces_session_and_expiry(tmp_path):
    from agent.images.service import ImageAssetError, ImageAssetService

    repo = _FakeMetadataRepo()
    service = ImageAssetService(metadata_repo=repo, minio_repo=_FakeObjectStore(), fallback_root=tmp_path)
    asset = (await service.ingest_many([_png_upload()], user_id="u1", session_id="s1"))[0]

    with pytest.raises(ImageAssetError) as session_exc:
        await service.resolve_many([asset["image_id"]], user_id="u1", session_id="other")
    assert session_exc.value.code == "IMAGE_FORBIDDEN"

    repo.rows[asset["image_id"]]["expires_at"] = "2000-01-01T00:00:00+00:00"
    with pytest.raises(ImageAssetError) as expiry_exc:
        await service.resolve_many([asset["image_id"]], user_id="u1", session_id="s1")
    assert expiry_exc.value.code == "IMAGE_NOT_FOUND"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_asset_service_rebuilds_preview_from_sidecar_when_db_row_incomplete(tmp_path):
    from agent.images.service import ImageAssetService

    repo = _FakeMetadataRepo()
    first = ImageAssetService(metadata_repo=repo, minio_repo=_FakeObjectStore(), fallback_root=tmp_path)
    asset = (await first.ingest_many([_png_upload()], user_id="u1", session_id="s1"))[0]
    repo.rows[asset["image_id"]].pop("preview_url", None)
    repo.rows[asset["image_id"]].pop("filename", None)

    recovered = ImageAssetService(metadata_repo=repo, minio_repo=None, fallback_root=tmp_path)
    resolved = await recovered.resolve_many([asset["image_id"]], user_id="u1", session_id="s1")
    assert resolved[0]["preview_url"].endswith("session_id=s1")
    assert resolved[0]["filename"] == "成绩图.png"