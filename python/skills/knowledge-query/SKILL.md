---
name: knowledge-query
description: 基于知识库（学生手册 + 个人成绩单）回答学生关于大学校园生活、规章制度和个人学业的问题。当用户查询学校制度、管理规定、个人学业成绩时使用。 何时不用：需要实时新闻/外部资料时请用 web-search；本技能只查内部知识库（手册+个人成绩单）。
allowed_tools: [adaptive_knowledge_retrieve]
---

## Description

知识库问答：主 Agent 调用 `adaptive_knowledge_retrieve`，由检索控制器在授权范围内完成选库、query 改写、dense+lexical 混合召回、RRF 与片段筛选；最终按 evidence/citations 引用来源作答。旧 `query_handbook/query_transcript` 仅保留内部兼容。

> 2026-08-25：v0.9 重构把原 `query_knowledge`（一个工具 + 混合 user_ids）拆成 `query_handbook` 与 `query_transcript`。两类不同问题（公开 vs 个人）不应共用 top_k 候选集，否则会污染精度 + 模糊权限边界。详见 `docs/v2.0.0/notes/2026-08-25-knowledge-tools-split.md`。

## Trigger

用户查询学校制度/管理规定/校园生活/个人学业成绩时激活。触发关键词：

- **手册类 → query_handbook**：制度/手册/转专业/奖学金/宿舍/学分/毕业条件/借阅/校历/校规
- **个人类 → query_transcript**：我修过哪些课/某科成绩/我的绩点/我的 GPA/我的成绩单
- **混合覆盖**：两类都涉及就异步调用两次工具，分别答

## Architecture（按序加载）

1. Rules（先读边界，再行动）：
   - [Load Shared Rules: identity](../_shared/rules/identity.md)
   - [Load Shared Rules: grounding](../_shared/rules/grounding.md)
   - [Load Rules: knowledge-boundary](./rules/knowledge-boundary.md)
2. Commands（执行流程）：
   - [Load Command: answer-from-kb](./commands/answer-from-kb.md)
3. Scripts（调用示例，按需引用）：
   - [Load Script: query-example](./scripts/query-example.md)
