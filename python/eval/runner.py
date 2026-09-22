# -*- coding: utf-8 -*-
"""评估运行器 v2 — 消费 eval_sets（规范 v2），执行断言式/检索式指标，输出指标矩阵报告。

指标分层（对齐 RAGAS / LangSmith evaluator 类型）：
- exact/code（Phase 2）：tool_chain / numeric / reference / recall@k / context recall·precision
- llm（Phase 4 预留）：faithfulness / answer_relevancy / rubric（--judge 开关，未实装时提示）

用法：
  python eval/runner.py --set chat_intent                 # 断言式
  python eval/runner.py --set chat_intent --live          # 调真实端点
  python eval/runner.py --set chat_intent --judge         # 追加 LLM-as-judge（Phase 4）
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from datetime import date
from pathlib import Path

# eval/ 子目录运行时把项目根加入 sys.path（tools/ 等模块依赖）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL_SETS = Path(__file__).resolve().parent.parent / "eval_sets"
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
BASE = "http://localhost:8000"  # live 模式的 API 基址

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def load_set(name: str) -> list[dict]:
    path = EVAL_SETS / f"{name}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"eval set 不存在: {path}")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 确定性断言器（exact/code mode） ─────────────────────────────────────
def run_assertions(case: dict, output: dict) -> tuple[bool, list[str]]:
    """执行 case.assertions（权重求和，得分 ≥ judge.threshold 通过）。"""
    failures: list[str] = []
    score = 0.0
    total_weight = 0.0
    for a in case.get("assertions", []):
        kind = a["kind"]
        weight = float(a.get("weight", 1.0))
        total_weight += weight
        ok, err = _assert_one(kind, a, output)
        if ok:
            score += weight
        else:
            failures.append(err)
    if not case.get("assertions"):
        # 无断言时回退 judge 指标
        judge = case.get("judge", {})
        if judge.get("mode") in ("exact", "code"):
            ok, err = _judge_one(judge, case, output)
            return (ok, [err] if err else [])
        return True, []
    threshold = float(case.get("judge", {}).get("threshold", 1.0))
    passed = (score / total_weight) >= threshold if total_weight else True
    return passed, failures


def _assert_one(kind: str, a: dict, output: dict) -> tuple[bool, str]:
    field = a.get("field", "")
    if kind == "contains":
        value = str(a.get("value", ""))
        actual = str(_dig(output, field) or "")
        return (value in actual), f"contains {field}={value} 未命中: {actual[:80]}"
    if kind == "not_contains":
        value = str(a.get("value", ""))
        actual = str(_dig(output, field) or "")
        return (value not in actual), f"not_contains {field}={value} 出现: {actual[:80]}"
    if kind == "numeric":
        want = float(a["value"])
        tol = float(a.get("tolerance", 0.01))
        actual = _dig(output, field)
        if actual is None:
            return False, f"numeric {field} 无输出"
        return (abs(float(actual) - want) <= tol), f"numeric {field} 期望 {want} 实得 {actual}"
    if kind == "reference":
        # 数值引用核验：输出文本中数字必须 ∈ 白名单（容差 0.5）
        data_nums = [float(x) for x in a.get("value", [])]
        text = str(_dig(output, field) or "")
        for num_str in _NUM_RE.findall(text):
            v = float(num_str)
            if not any(abs(v - d) <= 0.5 for d in data_nums):
                return False, f"reference 输出数字 {num_str} 不在数据源"
        return True, ""
    if kind == "recall":
        hits = output.get("hit_chunk_ids", [])
        expected = set(a.get("value", []))
        if not expected:
            return True, ""
        hit = len(expected & set(hits)) / len(expected)
        min_recall = float(a.get("min_recall", a.get("k", 5) and 0.6))
        return (hit >= min_recall), f"recall@{a.get('k', 5)}={hit:.2f}"
    if kind == "count_ge":
        items = _dig(output, field)
        want = float(a.get("value", 0))
        if isinstance(items, list):
            return (len(items) >= want), f"count_ge {field}={len(items)} < {want}"
        if isinstance(items, (int, float)):
            return (float(items) >= want), f"count_ge {field}={items} < {want}"
        return False, f"count_ge {field} 非列表/数字"
    if kind == "count_le":
        items = _dig(output, field)
        want = float(a.get("value", 0))
        if isinstance(items, list):
            return (len(items) <= want), f"count_le {field}={len(items)} > {want}"
        if isinstance(items, (int, float)):
            return (float(items) <= want), f"count_le {field}={items} > {want}"
        return False, f"count_le {field} 非列表/数字"
    if kind == "is_error":
        got = bool(_dig(output, field))
        want = bool(a.get("value", True))
        return (got == want), f"is_error 期望 {want} 实得 {got}"
    if kind == "tool_chain":
        got = list(output.get("tool_chain", []))
        want = list(a.get("value", []))
        # 意图路由语义是"被调用的工具集合"：agent 多次调用同一工具属正常行为，
        # 断言去重后的包含关系（want 的所有工具均被调用过）。
        got_set, want_set = set(got), set(want)
        if not want_set:
            return (not got_set), f"tool_chain 期望空 实得 {got}"
        return want_set.issubset(got_set), f"tool_chain 期望 {want} 实得 {got}"
    return True, ""


def _judge_one(judge: dict, case: dict, output: dict) -> tuple[bool, str]:
    """judge 指标（exact/code 类）：tool_chain / numeric / reference / recall。"""
    metric = judge.get("metric", "")
    if metric == "tool_chain":
        got = list(output.get("tool_chain", []))
        want = list(case.get("expected", {}).get("tool_chain", []))
        got_set, want_set = set(got), set(want)
        if not want_set:
            return (not got_set), "" if not got_set else f"tool_chain 期望空 实得 {got}"
        ok = want_set.issubset(got_set)
        return ok, "" if ok else f"tool_chain 期望 {want} 实得 {got}"
    if metric == "recall":
        hits = output.get("hit_chunk_ids", [])
        expected = set(case.get("expected", {}).get("chunk_ids", []))
        if not expected:
            return True, ""
        hit = len(expected & set(hits)) / len(expected)
        threshold = float(judge.get("threshold", 0.6))
        return (hit >= threshold), f"recall@{judge.get('k', 5)}={hit:.2f} < {threshold}"
    if metric == "numeric":
        # 取第一个 numeric 断言口径
        for a in case.get("assertions", []):
            if a["kind"] == "numeric":
                return _assert_one("numeric", a, output)
        return True, ""
    if metric == "reference":
        for a in case.get("assertions", []):
            if a["kind"] == "reference":
                return _assert_one("reference", a, output)
        return True, ""
    return True, ""


# ── 检索式指标（code mode）：context recall / precision ──────────────────
def context_metrics(case: dict, output: dict) -> dict:
    """context recall（应命中是否召回）+ context precision（召回中相关占比）。"""
    expected = set(case.get("expected", {}).get("chunk_ids", case.get("reference", {}).get("contexts", [])))
    hits = output.get("hit_chunk_ids", [])
    if not expected:
        return {"context_recall": None, "context_precision": None}
    hit_set = set(hits) & expected
    recall = len(hit_set) / len(expected)
    precision = len(hit_set) / len(hits) if hits else 0.0
    return {"context_recall": round(recall, 3), "context_precision": round(precision, 3)}


def _dig(obj, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# ── 执行器 ───────────────────────────────────────────────────────────────
def execute_case(case: dict, *, live: bool, judge: bool) -> dict:
    t0 = time.perf_counter()
    if not live:
        output = _smoke_output(case)
        ok, failures = run_assertions(case, output)
        metrics = context_metrics(case, output) if case["type"] == "kb_retrieval" else {}
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": metrics,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "smoke",
        }
    try:
        return _execute_live_case(case, t0)
    except Exception as exc:  # noqa: BLE001
        # API 不可用/外部依赖异常 → 结构化失败，不 crash 整个 runner
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": False, "failures": [f"live 执行异常: {str(exc)[:120]}"], "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
        }


def _execute_live_case(case: dict, t0: float) -> dict:
    if case["type"] == "kb_retrieval":
        output = _live_kb(case["input"]["query"], case["input"].get("top_k", 5))
        ok, failures = run_assertions(case, output)
        metrics = context_metrics(case, output)
        k = case["input"].get("top_k", 5)
        expected = set(case.get("expected", {}).get("chunk_ids", []))
        # Phase 4 G1：NDCG@k / F1@k（_live_kb 已透出 rank/score）
        metrics["ndcg_at_k"] = _ndcg_at_k(output.get("ranked", []), expected, k)
        metrics["f1_at_k"] = _f1_at_k(output.get("hit_chunk_ids", []), expected, k)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": metrics,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"hits={len(output.get('hit_chunk_ids', []))}",
        }
    if case["type"] == "rag_agentic":
        output = _live_rag_agentic(case["input"])
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"status={output.get('status', '')} evidence={output.get('evidence_count', 0)} planner={output.get('planner_source', '')}",
        }
    if case["type"] == "web_search":
        output = _live_web_search(case["input"]["query"], case["input"].get("max_results", 3))
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"results={len(output.get('results', []))} src={output.get('source', '')}",
        }
    if case["type"] == "image_recognize":
        output = _live_image_recognize(case["input"])
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"kind={output.get('kind', '')} source={len(output.get('source_images', []))} err={bool(output.get('isError'))}",
        }
    if case["type"] == "image_generate":
        output = _live_image_generate(case["input"])
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"images={len(output.get('image_urls', []))} err={bool(output.get('error'))}",
        }
    if case["type"] == "chat_intent":
        output = _live_chat(case["input"], case["case_id"])
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": output.get("usage", {}),
            "api_latency_ms": output.get("latency_ms"),
            "ttft_ms": output.get("ttft_ms"),
            "detail": str(output.get("reply", ""))[:100],
        }
    if case["type"] == "report_math":
        output = _live_report_math(case)
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": {},
            "detail": f"students={output.get('students_count', 0)} failed={output.get('failed_count', 0)} batch={bool(output.get('batch_id'))}",
        }
    if case["type"] == "evaluation_comment":
        output = _live_evaluation(case)
        ok, failures = run_assertions(case, output)
        return {
            "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
            "pass": ok, "failures": failures, "metrics": {},
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "mode": "live",
            "usage": output.get("usage", {}),
            "api_latency_ms": output.get("latency_ms"),
            "detail": str(output.get("comment", "") or output.get("error", ""))[:100],
        }
    return {
        "case_id": case["case_id"], "type": case["type"], "difficulty": case.get("difficulty", ""),
        "pass": False, "failures": ["live 模式暂未覆盖该类型"], "metrics": {},
        "latency_ms": 0, "mode": "live",
    }


def _set_dig(obj: dict, dotted: str, value) -> None:
    """按点路径写入嵌套 dict（自引用 smoke 用）。"""
    parts = dotted.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _smoke_output(case: dict) -> dict:
    """smoke 输出构造：
    - evaluation_comment：用 input.comment（保留正例/反例的真实语义，幻觉必须被拦）
    - 其余类型：断言自引用填充（验证断言器与报告管道，不验证数据自洽）
    """
    t = case["type"]
    out: dict = {}
    if t == "evaluation_comment":
        out["comment"] = (case.get("input") or {}).get("comment", "")
    if t == "chat_intent":
        out["tool_chain"] = case.get("expected", {}).get("tool_chain", [])
    if t == "kb_retrieval":
        out["hit_chunk_ids"] = case.get("expected", {}).get("chunk_ids", [])
    if t == "image_recognize":
        # 预置识别输出（input.recognized）：正例=结构化 JSON，反例=结构化错误，保持容错语义
        recognized = (case.get("input") or {}).get("recognized") or {}
        if isinstance(recognized, dict):
            out.update(recognized)

    for a in case.get("assertions", []):
        kind = a["kind"]
        field = a.get("field", "")
        value = a.get("value", "")
        if kind == "contains":
            _set_dig(out, field, value)
        elif kind == "not_contains":
            _set_dig(out, field, "__ABSENT__")
        elif kind == "numeric":
            _set_dig(out, field, value)
        elif kind == "reference":
            # evaluation_comment 的 comment 已由 input 构造（保留幻觉反例语义），不覆盖
            if t == "evaluation_comment" and field == "comment":
                continue
            _set_dig(out, field, f"文本包含数字 {value[0] if value else 0}")
        elif kind == "recall":
            _set_dig(out, field, value)
        elif kind == "count_ge":
            _set_dig(out, field, value)
        elif kind == "count_le":
            _set_dig(out, field, value)
        elif kind == "is_error":
            _set_dig(out, field, bool(value))
        elif kind == "tool_chain":
            out["tool_chain"] = value
    return out


def _live_kb(query: str, top_k: int) -> dict:
    """真实手册检索（B3 断链修复：query_knowledge 已删 → query_handbook 公开分区）。

    透出每命中的 rank/score（G1 NDCG 计算需要）。
    """
    import asyncio
    import json as _json

    from agent import runtime
    from tools.knowledge.query_handbook import query_handbook

    async def _run() -> str:
        if runtime.document_vector_repo is None:
            await runtime.init()
        return await query_handbook.ainvoke({"query": query, "top_k": top_k})

    result = asyncio.run(_run())
    text = str(result)
    ids: list[str] = []
    ranked: list[dict] = []
    try:
        data = _json.loads(text)
        for m in data.get("matches", []) or []:
            cid = m.get("chunk_id")
            if cid:
                ids.append(str(cid))
                ranked.append(
                    {
                        "rank": int(m.get("rank", len(ranked) + 1)),
                        "chunk_id": str(cid),
                        "score": m.get("score"),
                    }
                )
    except _json.JSONDecodeError:
        ids = re.findall(r"chunk_id[=:]\s*[\"']?([\w:]+)", text)
    return {"hit_chunk_ids": ids, "ranked": ranked}


def _live_image_recognize(inputs: dict) -> dict:
    """真实图片链路：本地图片/图库 → 私有资产 → image_recognize → qwen3-vl-plus。"""
    import asyncio
    import json
    import os
    from io import BytesIO
    from pathlib import Path

    from fastapi import UploadFile
    from agent.main.context import user_context

    repo_root = Path(__file__).resolve().parents[2]
    image_path = str(inputs.get("image_path", "") or "").strip()
    search_dir = os.getenv("VISION_LIVE_IMAGE_DIR", "").strip()
    if not image_path and search_dir:
        directory = Path(search_dir)
        if directory.is_dir():
            candidates = sorted(
                p for p in directory.iterdir()
                if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
            )
            if candidates:
                image_path = str(candidates[0])
    if not image_path:
        image_path = str(repo_root / "docs" / "v2.0.0" / "image.png")
    path = Path(image_path)
    if not path.is_absolute():
        path = repo_root / path
    if not path.is_file():
        return {"isError": True, "code": "IMAGE_NOT_FOUND", "message": f"live image not found: {path}"}

    from agent.images.service import ImageAssetService
    from tools.image.image_recognize import image_recognize
    from agent import runtime

    async def _run() -> dict:
        user_id = str(inputs.get("user_id", "eval-vision"))
        session_id = str(inputs.get("session_id", "eval-vision"))
        service = ImageAssetService(metadata_repo=None, minio_repo=None)
        upload = UploadFile(
            filename=path.name,
            file=BytesIO(path.read_bytes()),
            headers={"content-type": "application/octet-stream"},
        )
        assets = await service.ingest_many([upload], user_id=user_id, session_id=session_id)
        old_service = getattr(runtime, "image_asset_service", None)
        runtime.image_asset_service = service
        try:
            with user_context(user_id):
                raw = await image_recognize.ainvoke(
                    {
                        "image_ids": [assets[0]["image_id"]],
                        "question": str(inputs.get("question", "")),
                        "mode": str(inputs.get("mode", "auto")),
                    }
                )
        finally:
            runtime.image_asset_service = old_service
            await service.delete(assets[0]["image_id"], user_id=user_id, session_id=session_id)
        try:
            data = json.loads(str(raw))
            return data if isinstance(data, dict) else {"result": str(raw), "isError": False}
        except json.JSONDecodeError:
            return {"result": str(raw), "isError": False}

    return asyncio.run(_run())


def _live_rag_agentic(inputs: dict) -> dict:
    """真实 adaptive_knowledge_retrieve：planner + hybrid RRF + rerank。"""
    import asyncio
    import json

    from agent import runtime
    from agent.main.context import user_context
    from tools.knowledge.adaptive_retrieve import adaptive_knowledge_retrieve

    async def _run() -> dict:
        if runtime.document_vector_repo is None:
            await runtime.init()
        user_id = str(inputs.get("user_id", ""))
        with user_context(user_id):
            raw = await adaptive_knowledge_retrieve.ainvoke(
                {
                    "question": str(inputs.get("question", "")),
                    "requested_kbs": list(inputs.get("requested_kbs", []) or []),
                    "max_rounds": int(inputs.get("max_rounds", 1)),
                }
            )
        data = json.loads(str(raw))
        evidence = data.get("evidence", []) or []
        data["evidence_count"] = len(evidence)
        data["citation_count"] = len(data.get("citations", []) or [])
        data["planner_source"] = (data.get("trace") or {}).get("planner", "")
        data["hit_chunk_ids"] = [str(item.get("chunk_id", "")) for item in evidence if item.get("chunk_id")]
        return data

    return asyncio.run(_run())


def _live_web_search(query: str, max_results: int) -> dict:
    """真实 web_search（MCP 主路）→ 断言用输出（results + 拼接文本）。"""
    import asyncio
    import json

    from tools.chat.web_search import web_search

    from tools.mcp_client import reset_mcp_client

    reset_mcp_client()
    result = asyncio.run(web_search.ainvoke({"query": query, "max_results": max_results}))
    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return {"results": [], "joined": str(result)}
    results = data.get("results", [])
    joined = " ".join(f"{r.get('title', '')} {r.get('content', '')}" for r in results)
    return {"results": results, "joined": joined, "source": data.get("source", "")}


def _live_image_generate(inputs: dict) -> dict:
    """真实即梦两段式链路（submit → 轮询 get）→ 断言用输出（urls / error）。"""
    import asyncio
    import json

    from tools.image.image_generate import image_generate, image_generate_get

    async def _run() -> dict:
        # 每 case 独立 asyncio.run 循环：重置 MCP 客户端，避免 stdio 连接跨循环复用异常
        from tools.mcp_client import reset_mcp_client

        reset_mcp_client()
        r1 = json.loads(await image_generate.ainvoke(inputs))
        if r1.get("isError"):
            return {"error": True, "image_urls": [], "detail": str(r1)[:150]}
        tid = r1.get("task_id", "")
        for attempt in range(1, 11):
            r = json.loads(await image_generate_get.ainvoke({"task_id": tid, "attempt": attempt}))
            if r.get("status") == "done":
                return {"error": False, "image_urls": r.get("image_urls", [])}
            if r.get("isError"):
                return {"error": True, "image_urls": [], "detail": str(r)[:150]}
            await asyncio.sleep(r.get("next_poll_after_seconds", 3))
        return {"error": True, "image_urls": [], "detail": "轮询超时"}

    return asyncio.run(_run())


def _live_chat(inputs: dict, case_id: str = "") -> dict:
    """真实 chat 链路（/chat/stream）→ 工具调用序列 + 回复（LLM 实际路由即真值）。

    每 case 独立 session_id（避免续轮污染上下文）。
    dispatch_module 路由工具按 args.intent 映射为模块名（"report"/"evaluation"/...），
    这样 eval 期望的 `tool_chain: ["report"]` 与实际链路对齐；dispatch_module 自身从
    tool_chain 里过滤掉（避免双重计数）。
    """
    import httpx
    import uuid

    message = str(inputs.get("message", ""))
    user_id = str(inputs.get("user_id", "3123003252"))
    session_id = f"eval-{case_id or uuid.uuid4().hex[:6]}"
    body = {"message": message, "session_id": session_id, "user_id": user_id}
    if inputs.get("images"):
        body["images"] = list(inputs["images"])
    with httpx.stream("POST", f"{BASE}/api/v1/chat/stream", json=body, timeout=280) as resp:
        return _parse_chat_stream_events(resp.iter_lines())


def _parse_chat_stream_events(lines) -> dict:
    """SSE 行迭代器 → {tool_chain, reply, usage, latency_ms, ttft_ms}。

    暴露为模块级纯函数便于单测（mock SSE 流）。
    噪音过滤：read_file/write_file/edit_file/list_available_skills/get_current_time
    + tavily_* (web_search MCP 子事件) + execute_code (e2b 实现细节)。
    dispatch_module 路由按 args.intent 映射为模块名，自身过滤掉。
    """
    tools: list[str] = []
    reply = ""
    usage: dict = {}
    latency_ms: float | None = None
    ttft_ms: float | None = None
    event = ""
    for line in lines:
        if not line:
            continue
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: ") and event:
            try:
                data = json.loads(line[6:])
            except json.JSONDecodeError:
                data = {}
            if event == "tool" and data.get("status") == "start":
                tool_name = str(data.get("tool", ""))
                args = data.get("args") or {}
                if tool_name == "dispatch_module":
                    intent = args.get("intent") if isinstance(args, dict) else None
                    if intent:
                        tools.append(str(intent))
                    continue
                if tool_name == "task":
                    # deepagents task 委派：subagent_type=report_agent/evaluation_agent/...
                    # 映射回模块名（report/evaluation/ppt/recommendation），与 dispatch_module 的 intent 语义对齐
                    sub_type = (args.get("subagent_type") if isinstance(args, dict) else None) or ""
                    if sub_type.endswith("_agent"):
                        module = sub_type[: -len("_agent")]
                        tools.append(module)
                    elif sub_type:
                        tools.append(sub_type)
                    continue
                tools.append(tool_name)
            elif event == "text":
                reply += str(data.get("token", ""))
            elif event == "done":
                usage = data.get("usage", {}) or {}
                latency_ms = data.get("latency_ms")
                ttft_ms = data.get("ttft_ms")
            event = ""
    noise = {"read_file", "write_file", "edit_file", "list_available_skills", "get_current_time"}
    tools = [t for t in tools if t not in noise and not t.startswith("tavily_") and t != "execute_code"]
    return {
        "tool_chain": tools,
        "reply": reply[:2000],
        "usage": usage,
        "latency_ms": latency_ms,
        "ttft_ms": ttft_ms,
    }


def _live_report_math(case: dict) -> dict:
    """真实 report 端到端链路（POST /api/v1/report，真实样本）→ 断言用输出。

    消费 SSE：progress/student_done/student_error/done。done 事件含
    batch_id/students/failed_students；未收到 done（error 或超时）→ 结构化失败。
    与 eval-system.md 的 "report_math → /api/v1/report 端到端" 口径一致。
    """
    import httpx

    from pathlib import Path

    fixtures = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    candidates = sorted(fixtures.glob("*.xlsx"))
    if not candidates:
        return {"batch_id": "", "students_count": 0, "failed_count": 1, "has_batch_id": False, "students": [], "error": "no_fixture"}
    sample = candidates[0]

    fields = {"semester": case["input"].get("semester", ""), "user_message": case["input"].get("user_message", "")}
    files = [("files", (sample.name, sample.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))]
    batch_id = ""
    students: list[dict] = []
    failed_students: list[dict] = []
    error = ""
    t0 = time.perf_counter()
    with httpx.stream("POST", f"{BASE}/api/v1/report", data=fields, files=files, timeout=600) as resp:
        if resp.status_code >= 400:
            return {"batch_id": "", "students_count": 0, "failed_count": 1, "has_batch_id": False, "students": [], "error": f"http_{resp.status_code}"}
        event = ""
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    data = {}
                if event == "student_done":
                    students.append(data)
                elif event == "student_error":
                    failed_students.append(data)
                elif event == "done":
                    batch_id = str(data.get("batch_id", ""))
                    students = list(data.get("students", [])) or students
                    failed_students = list(data.get("failed_students", [])) or failed_students
                elif event == "error":
                    error = str(data.get("code", "") or data.get("message", ""))
                event = ""
    return {
        "batch_id": batch_id,
        "has_batch_id": bool(batch_id),
        "students": students,
        "students_count": len(students),
        "failed_count": len(failed_students),
        "error": error,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def _live_evaluation(case: dict) -> dict:
    """真实 evaluation 链路（/api/v1/evaluation）→ 评语/雷达/核验状态。

    按 case.input 参数化 target_user_id/comment_type；输出含 comment_length，
    无成绩单时返回 error=no_transcript_data（层①终止，不空跑 LLM）。
    """
    import httpx

    body = {
        "target_user_id": case["input"].get("target_user_id", "3123003252"),
        "comment_type": case["input"].get("comment_type", "semester_summary"),
        "generated_by": "eval-live",
    }
    comment = ""
    comment_status = ""
    radar_count = 0
    usage: dict = {}
    error = ""
    latency_ms: float | None = None
    t0 = time.perf_counter()
    with httpx.stream("POST", f"{BASE}/api/v1/evaluation", json=body, timeout=280) as resp:
        if resp.status_code >= 400:
            return {"comment": "", "comment_status": "", "radar_count": 0, "usage": {}, "latency_ms": 0, "error": f"http_{resp.status_code}"}
        event = ""
        for line in resp.iter_lines():
            if not line:
                continue
            if line.startswith("event: "):
                event = line[7:].strip()
            elif line.startswith("data: ") and event:
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    data = {}
                if event == "done":
                    comment = str(data.get("comment", ""))
                    comment_status = str(data.get("comment_status", ""))
                    radar = data.get("radar", {}) or {}
                    radar_count = len(radar.get("dimensions", []))
                    usage = data.get("usage", {}) or {}
                elif event == "error":
                    error = str(data.get("code", "") or data.get("message", ""))
                event = ""
    return {
        "comment": comment,
        "comment_status": comment_status,
        "status_ok": comment_status in ("llm", "rule"),
        "not_empty": bool(comment.strip()),
        "radar_count": radar_count,
        "comment_length": len(comment),
        "error": error,
        "usage": usage,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1) if latency_ms is None else latency_ms,
    }


# ── LLM-as-judge（Phase 4 B2/B3） ───────────────────────────────────────
JUDGE_CACHE_DIR = Path(__file__).resolve().parent / "judge_cache"


def _judge_output_for(case: dict, result: dict) -> dict:
    """judge 用的输出：live 用真实结果，非 live 用 smoke 输出（验证管道不耗额度）。"""
    if result.get("mode") == "live":
        return result
    return _smoke_output(case)


def _aggregate_judge(judge_results: list[dict]) -> dict:
    """judge 报告聚合：平均分/分档/逐 case/judge_failed 计数。"""
    metrics = ["faithfulness", "answer_relevancy", "rubric"]
    out: dict = {}
    failed = 0
    for metric in metrics:
        scores = [
            r["judge"][metric]["score"]
            for r in judge_results
            if r["judge"].get(metric) is not None and not r["judge"][metric].get("judge_failed")
        ]
        out[metric] = {
            "count": len(scores),
            "avg": round(statistics.mean(scores), 3) if scores else None,
        }
    for r in judge_results:
        for metric in metrics:
            jm = r["judge"].get(metric) or {}
            if jm.get("judge_failed"):
                failed += 1
    return {
        "evaluated": len(judge_results),
        "judge_failed_cases": failed,
        "by_metric": out,
    }


async def _run_judge(cases: list[dict], results: list[dict], *, model: str | None, sample: int | None, use_cache: bool) -> list[dict]:
    """对采样 case 跑 LLM-as-judge（触发矩阵见 eval/judge.py）。成本控制：采样 + 缓存。"""
    import random

    from eval.judge import judge_case

    cache_path = JUDGE_CACHE_DIR / "judge.json"
    cache: dict = {}
    if use_cache and cache_path.is_file():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache = {}

    indices = list(range(len(cases)))
    if sample and 0 < sample < len(indices):
        indices = random.Random(42).sample(indices, sample)  # 固定种子：结果可复现

    judge_results: list[dict] = []
    for i in indices:
        case = cases[i]
        result = results[i]
        out = _judge_output_for(case, result)
        key = case["case_id"]
        if use_cache and key in cache:
            judge_results.append({"case_id": key, "judge": cache[key]})
            continue
        j = await judge_case(case, out, model=model)
        judge_results.append({"case_id": key, "judge": j})
        if use_cache:
            cache[key] = j

    if use_cache:
        JUDGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return judge_results


# ── 报告聚合 ─────────────────────────────────────────────────────────────
def aggregate(results: list[dict]) -> dict:
    passed = sum(1 for r in results if r["pass"])
    latencies = [r["latency_ms"] for r in results if r["latency_ms"]]
    recalls = [r["metrics"]["context_recall"] for r in results if r["metrics"].get("context_recall") is not None]
    precisions = [r["metrics"]["context_precision"] for r in results if r["metrics"].get("context_precision") is not None]
    ndcgs = [r["metrics"]["ndcg_at_k"] for r in results if r["metrics"].get("ndcg_at_k") is not None]
    f1s = [r["metrics"]["f1_at_k"] for r in results if r["metrics"].get("f1_at_k") is not None]
    # token 消耗聚合（LLM 类集的 usage 回显）
    input_tokens = sum(int(r.get("usage", {}).get("input_tokens", 0) or 0) for r in results)
    output_tokens = sum(int(r.get("usage", {}).get("output_tokens", 0) or 0) for r in results)
    ttfts = [r["ttft_ms"] for r in results if r.get("ttft_ms")]
    api_lats = [r["api_latency_ms"] for r in results if r.get("api_latency_ms")]
    # 按难度分档
    by_difficulty: dict[str, dict] = {}
    for r in results:
        d = r.get("difficulty", "unknown")
        bucket = by_difficulty.setdefault(d, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += 1 if r["pass"] else 0
    return {
        "total": len(results),
        "passed": passed,
        "pass_rate": round(passed / len(results), 3) if results else 0.0,
        "latency_p50": _pct(latencies, 0.5),
        "latency_p95": _pct(latencies, 0.95),
        "ttft_p50": _pct(ttfts, 0.5),
        "api_latency_p50": _pct(api_lats, 0.5),
        "tokens": {"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens},
        "context_recall_avg": round(statistics.mean(recalls), 3) if recalls else None,
        "context_precision_avg": round(statistics.mean(precisions), 3) if precisions else None,
        "ndcg_at_k_avg": round(statistics.mean(ndcgs), 3) if ndcgs else None,
        "f1_at_k_avg": round(statistics.mean(f1s), 3) if f1s else None,
        "by_difficulty": by_difficulty,
    }


def _ndcg_at_k(ranked: list[dict], expected: set, k: int) -> float:
    """DCG@k = Σ_{i=1..k} rel_i / log2(i+1)；NDCG@k = DCG@k / IDCG@k（Phase 4 G1）。"""
    import math

    if not expected or not ranked:
        return 0.0
    dcg = 0.0
    for i, item in enumerate(ranked[:k], start=1):
        rel = 1.0 if str(item.get("chunk_id", "")) in expected else 0.0
        dcg += rel / math.log2(i + 1)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(expected), k) + 1))
    return round(dcg / idcg, 3) if idcg else 0.0


def _f1_at_k(hits: list, expected: set, k: int) -> float:
    """F1@k = 2·P·R/(P+R)（Phase 4 G1）。"""
    hit = len(set(hits) & set(expected))
    precision = hit / k if k else 0.0
    recall = hit / len(expected) if expected else 0.0
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 3)


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    vals = sorted(vals)
    idx = min(len(vals) - 1, int(len(vals) * p))
    return round(vals[idx], 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="eval_sets 评估运行器 v2")
    parser.add_argument("--set", required=True, help="eval set 名（eval_sets/<name>.jsonl）")
    parser.add_argument("--live", action="store_true", help="live 模式（调真实端点）")
    parser.add_argument("--judge", action="store_true", help="LLM-as-judge（faithfulness/answer_relevancy/rubric，触发矩阵见 eval/judge.py）")
    parser.add_argument("--judge-model", default=None, help="judge 用模型（默认同主模型，可换便宜模型控量）")
    parser.add_argument("--judge-sample", type=int, default=None, help="每集采样 N 条跑 judge（成本控制）")
    parser.add_argument("--judge-cache", action="store_true", help="judge 结果落盘缓存，二次跑不重复花")
    parser.add_argument("--case", help="只跑指定 case_id（逗号分隔，如 intent_17）")
    args = parser.parse_args()

    judge_results: list[dict] = []

    cases = load_set(args.set)
    if args.case:
        wanted = {c.strip() for c in args.case.split(",") if c.strip()}
        cases = [c for c in cases if c["case_id"] in wanted]
        if not cases:
            print(f"!! --case {args.case} 在 {args.set} 中无匹配用例")
            raise SystemExit(1)
    results = [execute_case(c, live=args.live, judge=args.judge) for c in cases]
    if args.judge:
        import asyncio

        judge_results = asyncio.run(
            _run_judge(
                cases,
                results,
                model=args.judge_model,
                sample=args.judge_sample,
                use_cache=args.judge_cache,
            )
        )
    report = {
        "date": str(date.today()),
        "set": args.set,
        "mode": "live" if args.live else "smoke",
        "judge": args.judge,
        "judge_metrics": _aggregate_judge(judge_results) if args.judge else None,
        "metrics": aggregate(results),
        "results": results,
        "judge_results": judge_results if args.judge else None,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"{args.set}-{date.today()}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    m = report["metrics"]
    if args.judge:
        jm = report["judge_metrics"] or {}
        print(f"== judge: evaluated={jm.get('evaluated')} judge_failed={jm.get('judge_failed_cases')} by_metric={jm.get('by_metric')}")
    print(f"== eval set: {args.set} ({'live' if args.live else 'smoke'})")
    for r in results:
        flag = "PASS" if r["pass"] else "FAIL"
        usage = r.get("usage", {}) or {}
        tok = f"tok={usage.get('input_tokens', 0)}+{usage.get('output_tokens', 0)}" if usage else ""
        print(f"[{flag}] {r['case_id']} ({r['difficulty']}) {r['latency_ms']}ms {tok} {r.get('detail', '')[:60]} {r['metrics']} {r['failures'][:1]}")
    print(f"== {m['passed']}/{m['total']} passed | p50={m['latency_p50']}ms p95={m['latency_p95']}ms")
    print(f"   tokens={m['tokens']['total']} (in={m['tokens']['input']} out={m['tokens']['output']})"
          f" | ttft_p50={m['ttft_p50']}ms | api_p50={m['api_latency_p50']}ms")
    print(f"   context_recall={m['context_recall_avg']} context_precision={m['context_precision_avg']} ndcg_at_k={m['ndcg_at_k_avg']} f1_at_k={m['f1_at_k_avg']}")
    print(f"   by_difficulty={m['by_difficulty']}")
    print(f"report: {out}")


if __name__ == "__main__":
    sys.exit(main())
