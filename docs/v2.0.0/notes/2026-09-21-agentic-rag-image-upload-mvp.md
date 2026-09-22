# 2026-09-21：Agentic RAG Phase 1 与图片上传识别闭环实施日记

> 范围：本轮已实际落地的部分。完整 Agentic RAG 仍未全部完成，未完成项在本笔记最后明确列出。

## 背景与问题

- RAG 原来只有单轮、单 query、Milvus dense 语义检索，`query_handbook/query_transcript` 直接暴露给主 Agent，缺少 hybrid、RRF、rerank、多轮和统一 trace。
- 图片识别原来虽有 `image_recognize`，但前端没有真实上传入口，Chat 传的是本地路径，缺少私有资产 ID、owner/session 校验、历史回显和可靠兜底。
- 本轮目标：先把图片识别前后端闭环做完，同时落下 Agentic RAG 的 Phase 1 hybrid 底座，并用最小数据跑 pytest、smoke、live。

## 总体架构方案

- RAG 新主入口：`adaptive_knowledge_retrieve`，主 Agent 只暴露该高层工具，内部按授权知识库执行 planner、dense + lexical、RRF、listwise rerank 和 fallback。
- 图片链路：前端上传图片 → `POST /api/v1/chat/images/upload` → `ImageAssetService` 归一化/私有存储 → 返回 `image_id` → Chat 只注入 image_id/MIME/宽高 → `image_recognize(image_ids=...)` 再按 owner 取图调用 `qwen3-vl-plus`。
- 兜底分层：MinIO 优先、本地对象目录兜底；MySQL 元数据优先、sidecar JSON 兜底；RAG planner/reranker 失败时分别回退规则 plan 和 RRF 排序。

## 细节实现

- 图片资产：`python/agent/images/service.py` 支持 PNG/JPEG/WebP/GIF/BMP、单图 10MB、总大小 30MB、最多 4 张、EXIF 清理、最长边 2048、owner+session 校验、TTL 和删除。
- 图片 API：`python/api/chat_images.py` 提供上传、私有内容读取、删除；跨用户读取返回 403，私有 preview URL 带 `user_id/session_id`。
- Chat：`python/api/chat.py` 新增 `image_ids`，旧 data URL 兼容；注入给 Agent 的内容不含 base64、本地路径或外部 URL。
- 前端：`frontend/src/app/(main)/chat/page.tsx` 增加图片选择、粘贴、预览、删除、批次上传串行化和历史附件回显；`frontend/src/lib/chatImages.ts` 做 MIME/大小/数量校验。
- RAG：`python/tools/knowledge/adaptive_retrieve.py` 实现 LLM planner、handbook/transcript 授权交集、MySQL lexical、Milvus dense、RRF、listwise rerank fallback 和 evidence/citations。
- 数据模型：`sql/init-db.sql` 增加 `chat_attachments`、`chat_messages.attachments_json` 和 `document_chunks.content` 的 ngram FULLTEXT 索引。

## Debug 结论

- 容器启动 `NameError: Path is not defined`：根因是 `python/agent/runtime.py` 新增图片资产初始化时漏 import `Path`；补 `from pathlib import Path` 后启动正常。
- RAG lexical 一直降级：根因是 `DocumentRepository.search_lexical` 查询了 MySQL `document_chunks` 不存在的 `section` 列，FTS 与 LIKE fallback 都抛 `OperationalError`；移除该列后恢复。
- 中文长 query 仍无 lexical 命中：根因是 FTS/LIKE 对完整中文短语匹配过窄；增加 2-gram + LIKE 兜底，例如“奖学金申请条件”拆出“奖学/申请/条件”。
- live RAG planner/reranker 回退：当前 deepseek-v4.1-flash 免费额度耗尽，LLM 调用返回 403；系统按设计自动退回规则 plan 与 RRF，RAG live 仍 3/3 通过。
- 主 Agent chat SSE smoke 未通过：同样是主语言模型免费额度耗尽，在工具调用前返回结构化配额错误；这不是图片链路或 RAG 工具本身的失败。

## 测试与验证

- 后端：`python -m pytest tests/ -m "not slow" -q` → `533 passed, 4 deselected`。
- 前端：`npm test` → `166 passed`；`npm run lint` 与 `npm run build` 均通过。
- smoke：RAG 2/2、图片识别 3/3 通过。
- live RAG：3/3 通过；日志显示 dense=20、lexical=14/19/16、RRF 后 21/25/24。
- live 图片识别：3/3 通过，覆盖 describe/ocr/chart；使用仓库内 `docs/v2.0.0/image*.png`，因为本机 `C:\Users\24361\Pictures` 不存在。
- 图片 API smoke：上传成功，本人读取 200，跨用户读取 403。
- 未执行：真实主 Agent chat SSE 图片链路（受主 LLM 免费额度耗尽阻断）；完整 Agentic 多轮 Reviewer 与 citation binder 验收。

## 经验与后续

- 兜底要在每一层都能独立成立：对象存储、元数据、planner、reranker 都不能成为单点。
- 权限检查必须同时绑定 owner 与 session，不能只依赖不可猜测的 image_id。
- 中文 lexical 检索不能假设 MySQL ngram FTS 会命中完整自然语言 query，2-gram/LIKE 兜底是必要的。
- 后续待完成：Evidence Reviewer、citation binder 强审计、shadow/canary、token 级图片鉴权、会话删除联动清理、Vision Schema 修复调用、SSE 图片专用 meta。

## 追加排查：前端上传按钮“没渲染”

- 用户反馈：主智能体页面看不到图片上传组件。
- 证据：源码 `frontend/src/app/(main)/chat/page.tsx` 已包含 `Upload + PictureOutlined + image_ids`；但正在运行的 `frontend` Docker 镜像是 2 周前构建的旧镜像。
- 根因：源码已更新，容器未重建，用户访问到旧前端产物。
- 修复：按仓库约定执行 `docker compose -f docker-compose.yml -f docker-compose.pull-mirror.yml --profile frontend up -d --build frontend`，新镜像成功构建并启动。
- 验证：容器内 `.next` 构建产物已包含“上传图片：PNG/JPEG/WebP/GIF/BMP”和 `handleImageFiles`；新增 ChatPage 渲染测试，确认登录态下可查询到“图片”上传按钮。
- 使用说明：上传入口位于 `/chat` 智能对话页发送框左侧，登录后显示；根路由 `/` 是智能体入口卡片页，不承载 Chat 输入框。
