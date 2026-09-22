# Script: query-example（检索调用示例）

> 调用契约示例；参数细节以工具 docstring 为准。
>
> 2026-08-25：v0.9 把 query_knowledge 拆成 query_handbook / query_transcript 两个工具。
> 按问题域分发，不要混调。

## adaptive_knowledge_retrieve（推荐）

```json
{"question": "转专业流程和申请时间是什么？"}
```

> 工具会按当前用户身份自动限制 handbook/transcript 范围并返回 evidence/citations。

## query_handbook（仅内部兼容）

```json
{"query": "转专业流程", "top_k": 5}
```

## query_transcript（个人成绩单 / 强隔离）

```json
{"query": "大三上 修过课程 成绩", "top_k": 3}
```

## 混合问题（异步多次调用）

```json
{"query_handbook_call": {"query": "奖学金申请条件", "top_k": 5},
 "query_transcript_call": {"query": "我过去三年 GPA 申请奖学金够不够", "top_k": 3}}
```

## 回答模板
- 要点回答 + `[来源: 学生手册 第 X 页]`（手册类）/ `[来源: 个人成绩单]`（个人类）
- 检索不到：说明知识边界并建议咨询教务/上传新文档
