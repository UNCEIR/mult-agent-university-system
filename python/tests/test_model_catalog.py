# -*- coding: utf-8 -*-
"""model_catalog 单测（Phase 4 A7）：查找/回退/成本估算。"""

from __future__ import annotations

import pytest

from config.model_catalog import estimate_cost, get_model_meta


@pytest.mark.unit
def test_get_model_meta_known():
    meta = get_model_meta("deepseek-v4.1-flash")
    assert meta.context_window == 128000
    assert meta.max_tokens == 8192
    assert meta.cost_input > 0


@pytest.mark.unit
def test_get_model_meta_unknown_fallback():
    meta = get_model_meta("no-such-model")
    assert meta.context_window == 128000
    assert meta.max_tokens == 8192


@pytest.mark.unit
def test_get_model_meta_empty_fallback():
    assert get_model_meta("").context_window == 128000


@pytest.mark.unit
def test_estimate_cost_positive():
    cost = estimate_cost("deepseek-v4.1-flash", input_tokens=1000, output_tokens=500)
    assert cost > 0
    assert round(cost, 6) == 0.0015


@pytest.mark.unit
def test_estimate_cost_missing_usage_zero():
    assert estimate_cost("deepseek-v4.1-flash", input_tokens=0, output_tokens=0) == 0.0
