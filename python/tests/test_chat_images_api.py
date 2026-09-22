# -*- coding: utf-8 -*-
"""聊天图片上传/读取 API 契约测试。"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image


class _Repo:
    def __init__(self) -> None:
        self.rows = {}

    def create(self, asset):
        self.rows[asset["image_id"]] = dict(asset)
        return True

    def get(self, image_id):
        row = self.rows.get(image_id)
        return dict(row) if row else None

    def delete(self, image_id):
        self.rows.pop(image_id, None)
        return True


class _Store:
    def __init__(self) -> None:
        self.objects = {}

    def upload(self, key, data, *, content_type=None):
        self.objects[key] = data
        return key

    def download(self, key):
        return self.objects.get(key)

    def delete(self, key):
        self.objects.pop(key, None)


def _png_bytes() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (18, 12), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_upload_and_read_private_image(tmp_path):
    from agent.app import app
    from agent.images.service import ImageAssetService

    service = ImageAssetService(metadata_repo=_Repo(), minio_repo=_Store(), fallback_root=tmp_path)
    with patch("agent.runtime.image_asset_service", service):
        client = TestClient(app)
        uploaded = client.post(
            "/api/v1/chat/images/upload",
            files={"files": ("screenshot.png", _png_bytes(), "image/png")},
            data={"session_id": "s-img", "user_id": "u-img"},
        )
        assert uploaded.status_code == 200
        payload = uploaded.json()["data"]
        image = payload["images"][0]
        assert image["mime_type"] == "image/png"

        content = client.get(
            f"/api/v1/chat/images/{image['image_id']}/content",
            params={"user_id": "u-img", "session_id": "s-img"},
        )
        assert content.status_code == 200
        assert content.content.startswith(b"\x89PNG")

        forbidden = client.get(
            f"/api/v1/chat/images/{image['image_id']}/content",
            params={"user_id": "other", "session_id": "s-img"},
        )
        assert forbidden.status_code == 403