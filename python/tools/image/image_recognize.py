# -*- coding: utf-8 -*-
"""image_recognize — 基于私有图片资产 ID 的视觉模型识别工具。

安全边界：
- LLM 只传 image_id，不接收 data URL、本地路径或任意外部 URL。
- owner 校验由 ImageAssetService 统一执行。
- 真实图片在工具内部转 data URL 后送 qwen3-vl-plus，不进入主 Agent checkpoint。
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Literal

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.images.service import ImageAssetError, get_image_asset_service
from agent.main.context import get_current_user_id
from ai.llm_client import build_chat_openai
from ai.llm_task_name import LLMTaskName

logger = logging.getLogger(__name__)


class VisionPoint(BaseModel):
    x: str | None = None
    y: float | None = None
    raw_y: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class VisionSeries(BaseModel):
    name: str | None = None
    points: list[VisionPoint] = Field(default_factory=list)


class VisionResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    kind: str = "other"
    chart_type: str | None = None
    trend: str = "未知"
    series: list[VisionSeries] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class ImageRecognizeInput(BaseModel):
    """image_recognize 工具输入参数。"""

    image_ids: list[str] = Field(..., min_length=1, max_length=4, description="服务端生成的图片资产 ID")
    question: str = Field(default="", max_length=2000, description="针对图片的问题（可空=描述图片）")
    mode: Literal["auto", "describe", "ocr", "table", "chart", "compare"] = Field(
        default="auto",
        description="识别模式；MVP 支持 auto/describe/ocr/table/chart，compare 预留多图对比",
    )


def _build_vision_llm():
    from config import get_settings

    settings = get_settings()
    return build_chat_openai(
        temperature=float(getattr(settings, "vision_temperature", 0.1)),
        max_tokens=int(getattr(settings, "vision_max_tokens", 2048)),
        task_name=LLMTaskName.VISION_ANALYZE,
        model=settings.vision_model,
        request_timeout=float(getattr(settings, "vision_timeout_seconds", 60.0)),
        max_retries=int(getattr(settings, "vision_max_retries", 1)),
    )


_CHART_KEYWORDS = ("成绩", "趋势", "图表", "柱状", "折线", "雷达", "统计")
_TABLE_KEYWORDS = ("表格", "成绩单", "课表", "名单", "汇总表")
_OCR_KEYWORDS = ("识别文字", "提取文字", "ocr", "通知", "公告", "截图文字")

_STRUCTURED_PROMPT = """请把图片内容识别为结构化 JSON（只输出 JSON，不要多余文字）：
{{
  "chart_type": "line|bar|radar|table|other",
  "series": [{{"name": "系列名", "points": [{{"x": "标签", "y": 数值}}]}}],
  "trend": "上升|下降|波动|平稳|未知",
  "confidence": 0.0~1.0,
  "summary": "一句话总结"
}}
若图片不是图表/成绩单，chart_type=other，series=[]。
识别结果必须严格基于图片内容，看不清的字段写 null，禁止编造。
"""

_TABLE_PROMPT = """请严格提取图片中的表格内容，只输出 JSON：
{{"kind":"table","summary":"...","columns":["..."],"rows":[["..."]],"confidence":0.0}}
无法辨认的单元格写 null，禁止补全。"""

_OCR_PROMPT = """请按阅读顺序提取图片中可见的文字，保留数字、日期和标题。
只输出 JSON：{{"kind":"ocr","text":"...","confidence":0.0}}。
看不清的字写 null，禁止根据上下文猜测。"""


def _infer_mode(question: str, requested: str) -> str:
    if requested != "auto":
        return requested
    text = question or ""
    if any(kw in text for kw in _TABLE_KEYWORDS):
        return "table"
    if any(kw in text for kw in _CHART_KEYWORDS):
        return "chart"
    if any(kw.lower() in text.lower() for kw in _OCR_KEYWORDS):
        return "ocr"
    return "describe"


def _prompt_for(mode: str, question: str) -> str:
    if mode == "chart":
        base = _STRUCTURED_PROMPT
    elif mode == "table":
        base = _TABLE_PROMPT
    elif mode == "ocr":
        base = _OCR_PROMPT
    elif mode == "compare":
        base = "请比较多张图片的差异，只依据图片可见内容，无法确认处明确写未知。"
    else:
        base = "请详细描述图片的内容；如果图中包含文字/表格，请忠实摘录，看不清处注明。"
    return base + (f"\n补充问题：{question}" if question else "")


def _extract_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return None
    return data if isinstance(data, dict) else None


async def _load_image_data_urls(image_ids: list[str]) -> list[str]:
    user_id = get_current_user_id()
    if not user_id:
        raise ImageAssetError("IMAGE_FORBIDDEN", "未登录，无法访问图片附件", status_code=401)
    import agent.runtime as runtime

    service = getattr(runtime, "image_asset_service", None) or get_image_asset_service()
    data_urls: list[str] = []
    for image_id in image_ids:
        data, mime_type = await service.load(image_id, user_id=user_id)
        data_urls.append(f"data:{mime_type or 'image/png'};base64,{base64.b64encode(data).decode('ascii')}")
    return data_urls


@tool(args_schema=ImageRecognizeInput)
async def image_recognize(
    image_ids: list[str],
    question: str = "",
    mode: str = "auto",
) -> str:
    """识别当前用户上传的图片（仅接受 image_id，不接收 URL/本地路径）。

    何时用：用户上传图片并要求描述、OCR、表格提取、成绩趋势/图表分析。
    返回结构化 JSON 或文本；图表/表格/OCR 必须基于图片内容，无法辨认时拒绝编造。
    """
    try:
        data_urls = await _load_image_data_urls(image_ids)
    except ImageAssetError as exc:
        return json.dumps(exc.to_payload(), ensure_ascii=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("image asset resolve failed: %s", type(exc).__name__)
        return json.dumps(
            {"isError": True, "code": "IMAGE_FETCH_FAILED", "message": "图片读取失败，请重新上传"},
            ensure_ascii=False,
        )

    selected_mode = _infer_mode(question, mode)
    structured = selected_mode in {"chart", "table", "ocr"}
    content: list[dict] = [{"type": "text", "text": _prompt_for(selected_mode, question)}]
    content.extend({"type": "image_url", "image_url": {"url": data_url}} for data_url in data_urls)

    try:
        llm = _build_vision_llm()
        resp = await llm.ainvoke([HumanMessage(content=content)])
        raw = str(resp.content or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("vision analyze failed: %s", type(exc).__name__)
        return json.dumps(
            {"isError": True, "code": "VISION_FAILED", "message": "图片识别暂不可用，请稍后重试", "source_images": image_ids},
            ensure_ascii=False,
        )

    if not structured:
        return raw or json.dumps({"isError": True, "code": "VISION_EMPTY", "message": "图片识别结果为空"}, ensure_ascii=False)

    data = _extract_json(raw)
    if data is None:
        return json.dumps(
            {
                "isError": True,
                "code": "VISION_UNSTRUCTURED",
                "message": "识别结果非结构化 JSON，拒绝引用",
                "source_images": image_ids,
            },
            ensure_ascii=False,
        )
    data.setdefault("schema_version", "1.0")
    data.setdefault("kind", selected_mode)
    try:
        result = VisionResult.model_validate(data)
    except ValidationError:
        return json.dumps(
            {
                "isError": True,
                "code": "VISION_UNSTRUCTURED",
                "message": "识别结果未通过结构化校验，拒绝引用",
                "source_images": image_ids,
            },
            ensure_ascii=False,
        )
    payload = result.model_dump(exclude_none=True)
    payload["source_image"] = image_ids[0]
    payload["source_images"] = image_ids
    return json.dumps(payload, ensure_ascii=False)