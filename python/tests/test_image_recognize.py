# -*- coding: utf-8 -*-
"""image_recognize 单测：image_id 资产、结构化 JSON、可溯源与容错。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.images.service import ImageAssetError


def _llm_responding(text: str):
    llm = MagicMock()
    resp = MagicMock()
    resp.content = text
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


@pytest.mark.unit
async def test_recognize_structured_chart_returns_json_with_source():
    from tools.image.image_recognize import image_recognize

    llm = _llm_responding(
        '{"chart_type": "line", "series": [{"name": "成绩", "points": [{"x": "上学期", "y": 85}]}], '
        '"trend": "上升", "confidence": 0.9, "summary": "成绩呈上升趋势"}'
    )
    with (
        patch("tools.image.image_recognize._load_image_data_urls", new=AsyncMock(return_value=["data:image/png;base64,AAAA"])),
        patch("tools.image.image_recognize._build_vision_llm", return_value=llm),
    ):
        raw = await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345"], "question": "分析成绩趋势图", "mode": "chart"})

    data = json.loads(raw)
    assert data["chart_type"] == "line"
    assert data["trend"] == "上升"
    assert data["source_image"] == "img_abc123456789012345"
    assert data["source_images"] == ["img_abc123456789012345"]


@pytest.mark.unit
async def test_recognize_unstructured_rejected():
    from tools.image.image_recognize import image_recognize

    llm = _llm_responding("这段成绩还不错，整体平稳。")
    with (
        patch("tools.image.image_recognize._load_image_data_urls", new=AsyncMock(return_value=["data:image/png;base64,AAAA"])),
        patch("tools.image.image_recognize._build_vision_llm", return_value=llm),
    ):
        raw = await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345"], "question": "成绩趋势如何", "mode": "chart"})

    data = json.loads(raw)
    assert data.get("isError") is True
    assert data["code"] == "VISION_UNSTRUCTURED"
    assert data["source_images"] == ["img_abc123456789012345"]


@pytest.mark.unit
async def test_recognize_forbidden_asset_returns_error():
    from tools.image.image_recognize import image_recognize

    with patch(
        "tools.image.image_recognize._load_image_data_urls",
        new=AsyncMock(side_effect=ImageAssetError("IMAGE_FORBIDDEN", "无权访问该图片", status_code=403)),
    ):
        raw = await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345"], "question": ""})
    data = json.loads(raw)
    assert data.get("isError") is True
    assert data["code"] == "IMAGE_FORBIDDEN"


@pytest.mark.unit
async def test_recognize_plain_description_when_mode_describe():
    from tools.image.image_recognize import image_recognize

    llm = _llm_responding("这是一张校园照片。")
    with (
        patch("tools.image.image_recognize._load_image_data_urls", new=AsyncMock(return_value=["data:image/png;base64,AAAA"])),
        patch("tools.image.image_recognize._build_vision_llm", return_value=llm),
    ):
        raw = await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345"], "question": "", "mode": "describe"})

    assert raw == "这是一张校园照片。"


@pytest.mark.unit
async def test_recognize_multiple_images_builds_multiple_content_parts():
    from tools.image.image_recognize import image_recognize

    llm = _llm_responding("两张图都是通知。")
    with (
        patch("tools.image.image_recognize._load_image_data_urls", new=AsyncMock(return_value=["data:image/png;base64,AAAA", "data:image/png;base64,BBBB"])),
        patch("tools.image.image_recognize._build_vision_llm", return_value=llm),
    ):
        await image_recognize.ainvoke({"image_ids": ["img_abc123456789012345", "img_def123456789012345"], "question": "描述", "mode": "describe"})

    messages = llm.ainvoke.await_args.args[0]
    content = messages[0].content
    assert len([part for part in content if part.get("type") == "image_url"]) == 2