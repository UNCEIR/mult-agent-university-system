# Phase 3 真实端测兑现（未来承诺落地）

## 背景与问题

- 触发：phase-3-extensions.md §1.4 试金石第 58 行"未来承诺"——上游资源充足后必须严格跑真实端测并 eval 评估，作为 Phase 3 正式验收依据。
- 状态：Phase 3 编码 W-A~W-F 已完成（267 passed + Next.js build 通过），但因 LLM 算力受限（`qwen3.5-flash` 余额不足），真实端测 ⏳ 项延后。
- 上游更新：模型从 `qwen3.5-flash` 切换为 `deepseek-v4.1-flash`（已写 `python/.env`）；Docker registry-1.docker.io 不可达，改用 `docker.m.daocloud.io/library/python:3.12-slim` 镜像源（缓存复用 5/6 层）。
- 范围：用户要求"不要跑 phase3 未进行改动的功能 跑涉及调用新实装工具的功能 涉及记忆改造的功能"——故本轮仅跑：
  - `evaluation_comment_live`（直接走 Phase 3 实装的 evaluation 三 @tool + compute_weighted_grade）
  - `report_math_live`（端到端 SSE 消费 + compute_weighted_grade + mode 透传）
  - `chat_intent` 中 5 个失败 case（仅重测，不重复成功 15 个）
- 不纳入：`image_generate` / `web_search` / `kb_retrieval`（Phase 3 未改动的功能；`kb_retrieval` 顺手跑了作旁注但 recall 偏低，不计入正式兑现）。

## 总体架构方案

- **docker compose 重启链路**：mysql/redis/minio/etcd/milvus（基础设施）→ `python-api --build` 重建镜像（含新五字段 summarization prompt + evaluation 三 @tool + compute_weighted_grade 实装 + MemoryExtractWorker/consolidation）→ health=200 后 live eval。
- **eval runner live 模式**：`python/eval/runner.py --set <name> --live`。每 case 独立 session/超时；断言器（numeric/count_ge/recall/contains 等）聚合报告。
- **chat_intent live 路径**：`POST /api/v1/chat/stream` SSE 消费 text/tool/done/error 事件 → 提取 tool_chain → 与 `expected.tool_chain` 集合相等比对（Phase 3 不动意图路由层，故失败属既有问题）。
- **evaluation_comment_live live 路径**：`POST /api/v1/evaluation` SSE 消费 stage/radar/comment_token/done/error → 提取 comment/comment_status/radar_count → 断言 numeric/count_ge。
- **report_math_live live 路径**：`POST /api/v1/report` multipart SSE 消费 progress/student_done/student_error/done → 提取 batch_id/students/failed_students → 断言 numeric。

## 细节实现

### 修改文件
- `python/eval/runner.py` `_live_kb`：补充 `await runtime.init()`（让 `document_vector_repo` 在 fresh asyncio.run 中可用）+ JSON 解析 chunk_id（避免老版 `re.findall` 与 JSON 返回错配）。**仅在本次"顺手跑 kb_retrieval 作旁注"时使用，正式兑现不依赖此改动**。
- `docs/v2.0.0/plan.md` Phase 3 段尾追加"Phase 3 真实端测兑现（2026-08-18）"小节，回填试金石 #1/#5/#10 live 兑现证据。

### 运行命令（按用户"成功 15 个不管"原则）
```bash
# 失败 5 case 重测
cd python && python eval/runner.py --set chat_intent --live \
  --case intent_04,intent_05,intent_06,intent_07,intent_20

# 直接走新工具
cd python && python eval/runner.py --set evaluation_comment_live --live
cd python && python eval/runner.py --set report_math_live --live
```

## Debug 结论

- **问题 1**：`evaluation_comment_live` 首跑前要确认 Docker `python-api` 已重建（含 Phase 3 代码）。首次直接 `docker compose up -d python-api` 提示用了 `latest` tag（1.27GB，2026-08-17 14:38 构建），但本次代码改动后必须 rebuild。Dockerfile `ARG PYTHON_IMAGE=python:3.12-slim` 默认走官方 registry 不通，**改用 `--build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim` 重建**。
- **问题 2**：`kb_retrieval` `_live_kb` 提示 "document_vector_repo 不可用"。根因：fresh `asyncio.run` 不带 agent runtime。修复：调用前先 `await runtime.init()`；返回结果改 JSON 解析（工具实际返回 JSON 字符串）。**非正式兑现依赖，仅作旁注**。
- **问题 3**：`evaluation_comment_live` 输出 "71 门课/144.5 学分/加权 85.85"——`compute_weighted_grade` 公式正确（display×0.3 + exam×0.7 + bonus = 85.85），`metadata.user_id=3123003252` 真实成绩单可被评价端读出。Phase 3 实装验证通过。

## 测试与验证

| 集合 | 结果 | 备注 |
|------|------|------|
| `chat_intent`（仅 5 case 重测） | 1/5（`intent_20` 通过；其余 4 case 属意图路由既有问题，非 Phase 3 改动） | 报告：`eval/reports/chat_intent-2026-08-17.json` |
| `evaluation_comment_live` | **6/6 通过** | 报告：`eval/reports/evaluation_comment_live-2026-08-17.json` |
| `report_math_live` | **2/2 通过**（37 学生 PDF 全成） | 报告：`eval/reports/report_math_live-2026-08-18.json` |
| `kb_retrieval`（旁注） | 1/10（`kb_07` 通过；其它 recall 偏低但已命中真实 chunk_id——`refresh_kb_retrieval_oracle.py` 已采集 221 个真实 chunk_id 回填） | 报告：`eval/reports/kb_retrieval-2026-08-17.json` |

- **未执行**：未跑 phase3 未改动的功能（image_generate / web_search / report_math 原单元级断言 / chat_intent 15 个成功 case）——按用户指示。
- **未执行**：前端四 Page 的真实 UI 流式验证（`npm run dev` 冒烟需长时操作；plan §1.4 第 3 条 ⏳ 项保留延后）。

## 经验与后续

- **经验**：`--build-arg PYTHON_IMAGE` 必须在每次 rebuild 时显式传，Dockerfile 的 ARG 默认值会在 registry 不可达时卡住；DaoCloud 镜像源可用，缓存复用率高（5/6 层 CACHED）。
- **经验**：`/api/v1/chat/stream` 是真实链路统一入口；`kb_retrieval`/`evaluation_comment_live`/`report_math_live` 必须走 `/api/v1/...` 才能复用 lifespan 内的 runtime 单例，runner 直接 in-process 调工具会遇到 `document_vector_repo`/runtime 未初始化问题。
- **经验**：模型切换后 chat_intent 仍 4/5 失败，定位到意图路由 prompt 不动 → 非 Phase 3 范围，留 Phase 4 NLU 调优。
- **后续**：Phase 4 启动 NLU 调优专题（chat_intent 4 case 重做 prompt）；前端四 Page 流式事件断言补单测；`kb_retrieval` recall 提升（top_k/chunk_strategy 重训）。