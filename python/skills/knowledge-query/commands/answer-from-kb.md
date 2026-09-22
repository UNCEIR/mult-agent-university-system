# Command: answer-from-kb（检索与作答）

## Steps
1. 判断问题域：学校制度 / 课程修读 / 校园生活 / 个人学业 / 其他。
2. 调用 `adaptive_knowledge_retrieve(question=...)`；工具在授权分区内完成 hybrid 召回和 evidence 筛选。
3. 基于检索片段（content + 来源）组织回答，**引用来源**。
4. 检索为空 → 说明知识边界，不编造。
