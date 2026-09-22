# -*- coding: utf-8 -*-
"""聊天图片资产 API：上传、私有预览、删除。

图片不是公开产物，禁止复用 /api/v1/images/download 的公开无鉴权语义。
当前业务接口沿用 user_id 临时口径；Java 身份体系接入后应替换为 token 身份。
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response

from agent import runtime
from agent.images.service import ImageAssetError, get_image_asset_service

router = APIRouter()


def _service():
    return getattr(runtime, "image_asset_service", None) or get_image_asset_service()


def _public_asset(asset: dict) -> dict:
    """API 只暴露前端所需字段，隐藏 file_key/user_id 等内部寻址信息。"""
    return {
        "image_id": asset["image_id"],
        "mime_type": asset.get("mime_type", ""),
        "width": asset.get("width", 0),
        "height": asset.get("height", 0),
        "file_size": asset.get("file_size", 0),
        "filename": asset.get("filename", ""),
        "preview_url": asset.get("preview_url", ""),
        "expires_at": asset.get("expires_at", ""),
    }


def _http_error(exc: ImageAssetError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message})


@router.post("/api/v1/chat/images/upload")
async def upload_chat_images(
    files: list[UploadFile] = File(...),
    session_id: str = Form(...),
    user_id: str = Form(...),
):
    """上传 1-4 张聊天图片；支持 PNG/JPEG/WebP/GIF/BMP，服务端统一归一化。

    成功返回 image_id 列表；前端只传 image_id 给 Chat，不传 base64 或本地路径。
    """
    try:
        assets = await _service().ingest_many(
            files,
            user_id=user_id,
            session_id=session_id,
        )
    except ImageAssetError as exc:
        raise _http_error(exc) from exc
    return {"images": [_public_asset(asset) for asset in assets]}


@router.get("/api/v1/chat/images/{image_id}/content")
async def get_chat_image_content(
    image_id: str,
    user_id: str = Query(..., min_length=1, max_length=64),
    session_id: str = Query(..., min_length=1, max_length=64),
):
    """按 owner + session 读取私有图片；前端可使用 preview_url 直接展示。"""
    try:
        data, mime_type = await _service().load(image_id, user_id=user_id, session_id=session_id)
    except ImageAssetError as exc:
        raise _http_error(exc) from exc
    return Response(
        content=data,
        media_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Disposition": f'inline; filename="{image_id}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/api/v1/chat/images/{image_id}")
async def delete_chat_image(
    image_id: str,
    user_id: str = Query(..., min_length=1, max_length=64),
    session_id: str = Query(..., min_length=1, max_length=64),
):
    """删除当前用户图片；元数据不可破坏时采用 status=deleted 逻辑删除。"""
    try:
        ok = await _service().delete(image_id, user_id=user_id, session_id=session_id)
    except ImageAssetError as exc:
        raise _http_error(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail={"code": "IMAGE_NOT_FOUND", "message": "图片不存在"})
    return {"status": "ok", "image_id": image_id}