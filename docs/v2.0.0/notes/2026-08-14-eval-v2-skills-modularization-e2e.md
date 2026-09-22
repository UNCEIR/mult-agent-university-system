# eval 规范 v2 + skills 模块化 + 全接口 E2E 验证（2026-08-14）

## 背景与问题

- 本轮要解决的问题：
  1. eval_sets 构造过于简陋（字段单薄、中文被 `?` 破坏），需对齐市面主流评测规范（RAGAS / LangSmith / DeepEval / Promptfoo）
  2. skills/ 由单文件 SKILL.md 扩展为模块化目录（SKILL.md 变目录，逻辑下沉），且必须以 deepagents SkillsMiddleware 机制为兼容基准
  3. 多模态视觉模型换型为 qwen3-vl-plus 并做真实冒烟
  4. 对全部已闭环的 skill 功能接口做端到端测试
- 触发原因或用户诉求：用户要求"学习市面上优秀的 eval 评测规范进行更改"；"将单一 SKILL.md 扩展为 commands/rules/scripts 的解耦方案"；"对目前需要使用 skill 的功能接口且已闭环的都进行端到端测试"
- 影响范围：`python/eval_sets/`（50 case 重构）、`python/eval/runner.py`（指标矩阵升级）、`python/skills/`（10 个 skill 模块化 + `_shared/` 共享规则）、`python/api/chat.py`（images 附件接线修复）、`python/scripts/e2e_smoke.py`（新增冒烟脚本）、`python/config/settings.py` + `.env`（vision_model=qwen3-vl-plus、LLM_MODEL=deepseek-v4.1-flash）

## 总体架构方案

- 涉及模块：
  - eval 体系：`eval_sets/*.jsonl`（数据集层）+ `eval/runner.py`（执行层）+ `scripts/import_langsmith_dataset.py`（LangSmith 关联层）
  - skills 模块化：`skills/<name>/{SKILL.md, rules/, commands/, scripts/}` + `skills/_shared/rules/` 共享规则
  - chat 图片链路：`ChatRequest.images` → `_save_images` 落盘 → agent 消息注入 → `image_recognize` 工具
  - E2E：`scripts/e2e_smoke.py`（9 个闭环接口冒烟）
- 数据流或调用链：
  - eval v2：runner 读 JSONL（case_id/type/input/expected/reference/judge/assertions）→ 断言器（exact/code）→ 检索指标（context recall/precision）→ 聚合报告（通过率/延迟/分档）
  - skills 模块化：SkillsMiddleware 注入 frontmatter description → agent 按 SKILL.md Architecture 的 `[Load ...]` 标记 read_file 渐进加载 rules/commands/scripts
  - 图片链路：images(data URL) → 落盘 `.documents/chat_images/<session>/` → 注入"用户上传了图片附件（本地路径）"消息 → LLM 调 `image_recognize(image_url=本地路径)`
- 关键设计取舍：
  - eval v2 对齐业界：RAGAS 四指标（context recall/precision、faithfulness、answer relevancy）、LangSmith inputs/outputs/reference 三分量、DeepEval golden 结构、Promptfoo 断言权重——Phase 2 落地断言式+检索式，LLM-as-judge 接口预留（Phase 4）
  - scripts/ 定位：只放"多工具编排序列示例"（端到端调用 JSON），**不重复单工具 docstring**——避免与 tools/ 注释冗余臃肿
  - skills 共享规则用 `_shared/`（无 SKILL.md，SkillsMiddleware 只扫描含 SKILL.md 的一级目录，不会被误加载）
  - 运行时主链路保持 SkillsMiddleware + read_file，**不引入自定义 loader**

## 细节实现

- 修改或分析的关键文件：
  - `eval_sets/`：README.md（规范 v2：字段契约/指标矩阵/评估器分层/Registry/新增集规范）+ chat_intent(20)/report_math(10)/evaluation_comment(10)/kb_retrieval(10) 全部重写
  - `eval/runner.py`：断言器扩展（contains/not_contains/numeric/reference/recall/tool_chain + 权重求和）、context_metrics（recall/precision 手算可核对）、aggregate（分档/延迟 P50·P95）、`--judge` 预留、smoke 自引用构造（evaluation_comment 保留正反例真实语义）
  - `skills/`：10 个 skill 全部模块化（6 核心完整：recommend/report/evaluation/web-search/writing/image-generation；4 骨架：knowledge-query/document-ingestion/deep-thinking/ppt-generation）+ `_shared/rules/{identity,facts,fallback,grounding}.md`
  - `api/chat.py`：`_save_images`（data URL/URL → 落盘 → 路径列表）+ /chat 与 /chat/stream 注入图片附件消息
  - `scripts/e2e_smoke.py`：9 用例冒烟（chat 六链路 + report 全链 + evaluation + upload），PASS/FAIL 汇总
  - `python/skills/README.md` + `docs/v2.0.0/skills-tools-architecture.md §11`（模块化规范文档化）
  - `config/settings.py` + `.env`：`vision_model=qwen3-vl-plus`
- 核心逻辑：
  - eval v2 断言权重求和：`score/total_weight >= judge.threshold` 判过；evaluation_comment 含 4 个故意幻觉反例（02/04 必须被拦）
  - skills Architecture 路由：`[Load Shared Rules: identity](../_shared/rules/identity.md)` 相对路径引用
  - images 注入消息用 JSON 路径列表，agent 直接透传为 image_recognize 入参
- 兼容性与风险控制：SKILL.md frontmatter（name/description/allowed_tools）全部保持不变 → SkillsMiddleware 契约测试全绿；`_save_images` 失败静默跳过（尽力而为不阻塞对话）；`--skip-report` 选项控制慢项

## Debug 结论

1. **eval jsonl 中文全部变成 `?`**
   - 根因：PowerShell here-string 经管道传给 Python 时按 GBK 转码，中文字面量被破坏成 `?`，写入文件的就是 `?`（非显示问题）
   - 排查过程：`json.loads` 读取文件逐字段检查，scenario/description/reference 全为 `?`
   - 解决方式：改用 write 工具直接写 UTF-8 JSONL（绕开 PowerShell 编码路径）；后续含中文的生成一律走文件写入而非管道
   - 验证：重写后 `corrupted=False`，四个集中文完整
2. **chat 的 images 附件从未传给 agent**
   - 根因：G9 只定义了 `ChatRequest.images` 字段，/chat 与 /chat/stream 均未接线——图片链路断裂，agent 只能"口头建议"无法真正识别
   - 排查过程：E2E 冒烟时 chat→image_recognize 回复为"可使用图片识别功能"而非识别结果，检查 chat.py 消息构造发现 images 未进入 messages
   - 解决方式：`_save_images` 落盘 + 注入图片附件消息（声明可调 image_recognize），视觉工具读本地路径
   - 验证：E2E chat→image_recognize PASS（真实识别成功）
3. **eval smoke 对反例集不成立"自洽全绿"假设**
   - 根因：evaluation_comment 的幻觉反例（99/120 编造数字）在 smoke 模式下也走 input.comment，本应被拦截
   - 解决方式：smoke 输出分两类——chat/report/kb 用"断言自引用填充"（验证断言器与报告管道），evaluation_comment 保留 input.comment 真实语义（验证核验闸）
   - 验证：正例 01/03 过、反例 02/04 被拦

## 测试与验证

- 已执行：
  - `pytest tests/ -m "not slow"` = **236 passed, 4 deselected**（skills 模块化零破坏，契约测试全绿）
  - eval runner smoke：chat_intent 20/20、report_math 10/10、kb_retrieval 10/10（context_recall=1.0）、evaluation_comment 正例过反例拦
  - 视觉冒烟：容器内 image_recognize 识别需求文档截图成功（qwen3-vl-plus 多模态链路端到端）
  - E2E 全量（deepseek-v4.1-flash）：**9/9 通过**——chat→knowledge-query(10.5s) / recommend(148.9s) / writing(16.3s) / web-search(5.6s 降级链) / image-generation(3.7s 降级链) / image_recognize(19.8s 图片附件) / report 全链(621.5s，38 学生 PDF + token 下载 %PDF) / evaluation 全链(29.1s，生成 + /me) / documents upload(0.4s，251 chunks)
  - compileall 干净；docker 全服务健康
- 未执行及原因：LLM-as-judge（faithfulness/answer_relevancy/rubric）为 Phase 4 全量项（`--judge` 接口已预留）；真实 MCP 连接（tavily/即梦/E2B）凭据未到位（E2E 中验证的是降级链行为）

## 经验与后续

- 本轮经验：
  - PowerShell 管道是中文注入的隐形杀手——任何含中文的生成脚本都应走"文件写入"而非 here-string 管道（本会话第三次踩坑）
  - "字段定义了但没接线"是最隐蔽的假闭环——E2E 冒烟的价值就是戳穿它（images 字段存在、契约测试全绿，但真实链路断裂）
  - skills 模块化成功的关键前提是**先确认框架扫描语义**（SkillsMiddleware 只扫一级目录含 SKILL.md）——_shared 共享规则因此零风险
  - 反例进评测集（evaluation_comment 幻觉 case）比纯正例更能证明"防线真的在工作"
- 后续建议：
  - E2E 冒烟脚本纳入常规回归（每次接口变更后跑 `scripts/e2e_smoke.py --skip-report` 快速项）
  - Phase 4 实现 LLM-as-judge 时直接消费 eval_sets 的 reference.answer + judge.rubric
  - 4 个 Phase 3 骨架 skill（ppt-generation 等）实装时按同模板补全 commands/rules/scripts
  - skills 模块化后建议 chat 冒烟观察 read_file 子模块加载（trace 验证渐进披露实际生效）
