# eval 评估体系总述

> 本文档是 v2.0.0 **评估体系的总登记文档**：目录结构与职责、数据集 Registry、字段契约、测试链路闭环、运行方式与实测记录。
> **约定：后续新增 eval 评测统一在本文件登记**（新增数据集 → 在 §2 Registry 加一行；新指标/新执行器 → 在 §3/§4 补充；运行结果 → 追加到 §7 实测记录）。数据集文件本体在 `python/eval_sets/<name>.jsonl`，执行器在 `python/eval/runner.py`。

## 1. 目录结构与职责

```
python/eval_sets/           # 数据集层：JSONL 用例库（= 测试用例的声明式形态）
│   <name>.jsonl            # 每行一个 case（字段契约见 §3）
│   README.md               # 规范 v2（字段契约/指标矩阵/新增集规范）
python/eval/
│   runner.py               # 执行器：读 JSONL → smoke/live → 断言器 → 聚合报告
│   phase2/<set>-<date>.json # 运行报告（通过率/延迟/分档/逐 case 明细）
python/scripts/
│   import_langsmith_dataset.py  # JSONL → LangSmith Dataset（inputs/outputs/reference）
```

分层：**数据集层（声明期望）→ 执行器层（smoke 验证断言器 / live 验证真值）→ 报告层（指标聚合）→ LangSmith 层（Phase 4 LLM-as-judge）**。

## 2. 数据集 Registry

| 集 | case 数 | 场景来源 | 指标 | live 状态 |
|----|--------|---------|------|----------|
| chat_intent | 20 | 手写（四智能体+插件能力边界） | tool_chain 命中 / 意图准确率 | ✅ live |
| report_math | 10 | 真实样本行为反推（等级透传/多样性/分数丢弃/键合并/完整性/回填/坏响应/留空/Journal） | numeric / contains | ✅ live |
| evaluation_comment | 10 | 手写（正例/幻觉反例/规则兜底/无数据/维度校验/雷达手算/隔离/落库） | reference 核验 | ✅ live |
| kb_retrieval | 10 | 手册章节语义标注 | recall@k / context recall·precision | ✅ live |
| web_search | 5 | 手写 5 类真实查询（tavily 中文 400 约束已规避） | contains / count_ge | ✅ live |
| image_generate | 5 | 手写（单图/组图语义/scale 对照 0.5·0.7·0.9/违规容错） | count_ge / count_le / is_error | ✅ live |
| image_recognize | 4 | 手写（结构化趋势识别/成绩单表格/取图失败/非结构化拒绝） | contains / is_error | ⏳ live 依赖真实 images 附件（由 chat_intent intent_15/16 覆盖） |

**case 来源方法论**：① 手写场景（业务边界推导）；② 真实样本行为反推（管线确定性行为的手算期望）；③ 外部服务行为约束（tavily 纯中文 400 → query 加锚点；审核概率 → 容错观察）；④ 反例刻意为证（幻觉 99/120 必须被拦）。

## 3. case 字段契约速查

```json
{
  "case_id": "唯一ID", "type": "归属集", "scenario": "场景", "difficulty": "easy|medium|hard",
  "description": "人类可读说明",
  "input": { "请求输入" },
  "expected": { "期望行为（真值）" },
  "reference": { "answer": "理想回答", "contexts": ["参考上下文"], "keywords": [] },   // LLM-as-judge 用
  "judge": { "metric": "tool_chain|numeric|reference|recall|search", "mode": "exact|code|llm", "threshold": 0.6 },
  "assertions": [ {"kind": "contains|not_contains|numeric|reference|recall|count_ge|count_le|is_error|tool_chain", "field": "点路径", "value": ..., "weight": 1.0} ],
  "metadata": { "source": "handcrafted|fixture-derived|handbook-chunks", "created_at": "日期" }
}
```

## 4. 测试链路闭环

```
smoke（无外部依赖，验证断言器与报告管道）
  读 JSONL → _smoke_output（断言自引用构造输出；evaluation_comment 用 input.comment 真实语义保持反例被拦）
  → run_assertions（权重求和 ≥ judge.threshold）→ aggregate → 报告落盘

live（真实链路，验证真值）
  读同一 JSONL → 按 type 分派执行器：
    chat_intent     → /api/v1/chat/stream（收集 tool 事件序列 + 回复）
    report_math     → /api/v1/report（真实样本端到端：上传 → done → students/failed）
    evaluation_comment → /api/v1/evaluation（真实生成 → done 评语/雷达/核验状态）
    kb_retrieval    → query_knowledge 真实检索 → hit_chunk_ids
    web_search      → web_search 真实 MCP → results
    image_generate  → 两段式真实生成（submit → 轮询 get → done）
  → 断言器 → 聚合报告（mode=live 标记）

LangSmith（Phase 4 预留）
  import_langsmith_dataset.py → Dataset（inputs/outputs/reference 三分量）
  → LLM-as-judge（faithfulness/answer_relevancy/rubric）消费 reference；runner --judge 开关预留
```

**重要事实**：全部 6 集 live 均走项目内链路（内部 API/管线/知识库）或已打通的外部 MCP（tavily/即梦）——不存在"因外部依赖未到位而无法 live"的集。

## 5. 运行方式

```bash
cd python
python eval/runner.py --set <name>              # smoke（断言器管道自检）
python eval/runner.py --set <name> --live       # live（真实链路，需 API 运行中）
python eval/runner.py --set <name> --live --judge  # Phase 4 LLM-as-judge（预留）
python scripts/import_langsmith_dataset.py --set <name>  # 导入 LangSmith
```

## 6. 新增评测规范（登记约定）

1. 新建 `python/eval_sets/<name>.jsonl`（字段契约见 §3；反例必须进集；外部服务不确定性不作确定性断言）
2. `runner.py` 增加该 type 的 live 执行器（`_live_<name>`）与 smoke 输出构造
3. **在本文件 §2 Registry 加一行**（集名/case 数/来源/指标/live 状态），并把运行结果追加到 §7
4. 需要 LLM-as-judge 的集：case 必须带 `reference.answer` + `judge.rubric`

## 7. 实测记录

| 日期 | 集 | smoke | live | 备注 |
|------|----|-------|------|------|
| 2026-08-13/14 | chat_intent | 20/20 | 17/20（2026-08-15，deepseek-v4.1-flash） | 断言器管道正确；live：15 条路由正确；intent_15/16 需 images 附件（执行器支持后 PASS，deepseek-v4.1-flash 正确调 image_recognize）；**intent_17（code_interpreter）持续 FAIL**——qwen3.5-plus 与 deepseek-v4.1-flash 均直接给代码文本不调工具（真实行为偏差，待 prompt/skill 强化"执行类请求必须调工具"） |
| 2026-08-14 | report_math | 10/10 | 1/10（2026-08-15） | 工具层确定性断言（subject/grades）通过；fill/Journal 等单元级断言 live 无对应字段如实 FAIL（回归靠 smoke+单测） |
| 2026-08-14 | evaluation_comment | 正例过/反例拦 | 0/10（2026-08-15） | **链路真实工作**（每条 20-49s 真实生成、核验闸放行 status=llm、落库）；case 断言为虚构数据设计（data_numbers=[90.5...]），与真实输出（71 门课/148.5 学分）不匹配——live 需"真实数据版"case 集（按 §6 登记约定后续补充 `evaluation_comment_live.jsonl`） |
| 2026-08-14 | kb_retrieval | 10/10 | 0/10（2026-08-15） | `expected.chunk_ids` 为虚构标注（handbook_chunk_*），真实检索返回真实 chunk_id 体系（handbook_2025_acff6de8:N）——标注需按真实 chunk_id 重写才能做 recall 断言 |
| 2026-08-15 | web_search | 5/5 | 5/5 | tavily MCP 闭环（0.8-8.9s/用例） |
| 2026-08-15 | image_generate | 5/5 | 5/5 | 即梦两段式闭环 + scale 对照（30-130s/用例） |
| 2026-09-02 | image_recognize | 4/4 | 未跑 | 新增 smoke 集（结构化 chart/table 正例 + 取图失败/非结构化拒绝容错反例）；live 需真实 images 附件，暂由 chat_intent intent_15/16 覆盖 |

> **live 状态说明**：六集 live 全部走项目内链路或已打通的外部 MCP，不存在外部依赖阻塞；"未跑/部分"均为执行器覆盖（已补齐）或 case 断言与真实数据映射差异（如上表备注）。LLM 依赖的 live（chat_intent/evaluation_comment）需中转站额度可用。
