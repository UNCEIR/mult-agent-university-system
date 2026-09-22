# -*- coding: utf-8 -*-
"""模型元数据目录（Phase 4 P0-A，移植 pi model-auth-server 的 context/max/cost 概念）。

每个模型一行元数据：上下文窗口 / 最大输出 / 成本（每 1K token 计费，占位可调）。
压缩阈值、成本记账（M8）都从这里查；未收录模型回退缺省值（128000/8192）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelMeta:
    context_window: int
    max_tokens: int
    cost_input: float = 0.0      # 每 1K input tokens 成本（元）
    cost_output: float = 0.0     # 每 1K output tokens 成本（元）
    cost_tiers: dict = field(default_factory=dict)  # 预留：按调用场景/时段的计费分层


# 占位定价（公开市场价量级，可按实际账单修订）：元 / 1K tokens
_CATALOG: dict[str, ModelMeta] = {
    "deepseek-v4.1-flash": ModelMeta(
        context_window=128000, max_tokens=8192,
        cost_input=0.0005, cost_output=0.002,
        cost_tiers={"default": {"input": 0.0005, "output": 0.002}},
    ),
    "qwen3.8-max": ModelMeta(
        context_window=128000, max_tokens=8192,
        cost_input=0.004, cost_output=0.012,
        cost_tiers={"default": {"input": 0.004, "output": 0.012}},
    ),
    "qwen3-vl-plus": ModelMeta(
        context_window=32000, max_tokens=4096,
        cost_input=0.002, cost_output=0.006,
        cost_tiers={"default": {"input": 0.002, "output": 0.006}},
    ),
}

_DEFAULT_META = ModelMeta(context_window=128000, max_tokens=8192)


def get_model_meta(model: str) -> ModelMeta:
    """按模型名查元数据；未收录回退缺省（128000/8192）。"""
    if not model:
        return _DEFAULT_META
    return _CATALOG.get(model.strip(), _DEFAULT_META)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """估算一次调用成本（元）。usage 缺失时返回 0。"""
    meta = get_model_meta(model)
    return round(
        (int(input_tokens or 0) / 1000) * meta.cost_input
        + (int(output_tokens or 0) / 1000) * meta.cost_output,
        6,
    )
