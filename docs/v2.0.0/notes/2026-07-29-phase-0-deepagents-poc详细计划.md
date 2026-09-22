# Phase 0 详细计划：deepagents POC（go/no-go 门）

> 本文件是 `../plan.md` Phase 0 的**详细实施计划**，承接 `2026-07-28-设计决策补充说明.md` 决策 3 / 决策 15。Phase 0 是整个 v2.0.0 的**前置门控**——deepagents 与中转站、Docker 的兼容性是最大未验证风险（决策 3 已标注「实验性包，未本地源码核实」），POC 失败即触发回退。
>
> 日期：2026-07-29
> 状态：待执行
> 门控属性：**go/no-go**——通过方可进入 Phase 1 平台基座；不通过走回退决策。

---

## 1. 目标与范围

### 1.1 目标（三条最小验证轴）

| # | 验证轴 | 验证什么 | 对应决策 |
|---|--------|---------|---------|
| A | **deepagents 可用性** | 包能装上、`create_deep_agent` 能跑通一个最小 main agent + 1 个 tool 的 ReAct 循环 | 决策 3 |
| B | **中转站兼容** | deepagents 经 `ChatOpenAI`（`langchain-openai`）连 `one.zhique.cn/v1` + `deepseek-v4.1-flash`，能完成带 `bind_tools` 的 tool-calling（中转站对 OpenAI tool-calling 协议的兼容） | 决策 3、CLAUDE.md「LLM 与 Embedding 统一走中转站」 |
| C | **Docker 兼容** | POC 脚本在 `docker-compose.python.yml --profile python` 容器内跑通（构建层装得上 deepagents 依赖、运行时能出网到中转站） | 决策 15、CLAUDE.md「任何 Python 代码修改后 Docker 必须 `--build`」 |

### 1.2 范围（POC 刻意不做的事）

- **不**接 v1 推荐链路（`recommend_courses` tool 是 Phase 1 的事）
- **不**接 MinIO / FastGPT KB / MCP（Phase 1/3）
- **不**做 compaction / checkpointing / subagent 委派（决策 11/12，Phase 1+）
- **不**改 v1 任何代码（v1 内部零改动，决策 4）
- **不**做前端

POC 的工具用一个**确定性、无外部依赖**的 toy tool（如 `add(a, b)` 或 `echo`），隔离掉一切业务变量，只验证「deepagents 框架 ↔ 中转站 ↔ Docker」这条轴。

### 1.3 试金石（Definition of Done）

在 Docker 容器内执行一条 POC 命令，main agent 收到「请用工具算 3+5 并解释结果」这类指令后：

1. deepagents 自主决定调用 toy tool（证明 tool-calling loop 生效）
2. tool 返回 `8`，agent 读回结果并在最终回答中正确引用 `8`（证明中转站 tool-calling 协议双向兼容，不是只发不收）
3. 全程无 `httpx` SSL 报错、无 `bind_tools` 兼容报错、无 deepagents 导入报错

三条同时满足 = **GO**。

---

## 2. 风险与假设

### 2.1 已识别风险

| 风险 | 影响 | POC 如何暴露 | 触发回退？ |
|------|------|-------------|-----------|
| deepagents 是实验性包，API 可能与文档/记忆不符 | POC 无法启动 | 第 0 步 API 核对 + 第 1 步导入 | 若 API 完全不可用且无替代入口 → 回退 |
| 中转站 `one.zhique.cn` 对 OpenAI `tools`/`tool_choice` 协议支持不完整 | tool-calling 单向（只发不收）或报 400 | 第 3 步带 tool 的循环 | 是（决策 2 备选） |
| 中转站证书 SAN 不匹配 | `httpx` SSL 报错 | 第 2 步纯 LLM 调用 | 否（已知坑，`HTTPX_VERIFY_SSL=false` 解） |
| `enable_thinking=true`（`extra_body`）与 tool-calling 冲突 | 带工具时报错或不返回 tool call | 第 3 步 + 第 4 步对照 | 视情况：可关 thinking 规避则不算回退 |
| Docker 构建层装不上 `deepagents`/`langchain-mcp-adapters`（网络/依赖冲突） | 容器起不来 | 第 5 步 Docker 构建 | 否（调 requirements / 镜像源，见 §5） |
| deepagents 依赖的 LangGraph 版本与 v1 现有 `langgraph>=0.4.0` 冲突 | v1 链路被破坏 | 第 6 步回归 | 是（需锁定兼容版本范围） |

### 2.2 假设

- `python/.env` 已有可用 `LLM_API_KEY`（v1 已验证可调中转站，前提成立）
- 中转站对 `deepseek-v4.1-flash` 暴露 OpenAI 兼容 `/v1/chat/completions`（v1 已用 `ChatOpenAI` 验证）
- deepagents 建在 LangGraph `create_react_agent` 之上（决策 3 源码调研结论，POC 第 0 步核对）

---

## 3. 实施步骤

> 原则：**由内到外、逐轴加变量**。先本地裸 Python 验证 A+B，再套 Docker 验证 C。每步失败立即定位属于哪条轴。

### 第 0 步：核对 deepagents 公共 API（grounding）

deepagents 的具体导入签名与 `create_deep_agent` 参数尚未本地源码核实（决策 3 风险项）。动手前先核对官方仓库/文档，确认：

- 入口函数名（预期 `from deepagents import create_deep_agent`，但以官方为准）
- 参数形态（预期 `create_deep_agent(model, tools, system_prompt=...)`，返回 LangGraph `CompiledGraph`）
- 是否需要显式启用文件系统 / TodoWrite（预期默认开启）
- 依赖的 `langgraph` 最低版本，与 v1 `langgraph>=0.4.0` 是否兼容

**产出**：在本文件 §6「deepagents API 速记」补真实签名；若与下文伪码不符，下文伪码以实际为准。

### 第 1 步：本地最小 agent（裸 Python，验证轴 A 导入）

在 `python/scripts/poc_deepagents.py` 写一个不依赖 v1 任何模块的最小脚本：

```python
"""Phase 0 POC: deepagents + 中转站 + Docker 三轴验证。不依赖 v1 业务模块。"""
from __future__ import annotations

import httpx
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool

# 入口签名以第 0 步核对结果为准
from deepagents import create_deep_agent

from config import get_settings


@tool
def add(a: float, b: float) -> float:
    """Return the sum of two numbers. Use for any arithmetic addition."""
    return a + b


def build_poc_llm() -> ChatOpenAI:
    """复用 v1 的中转站配置（one.zhique.cn / deepseek-v4.1-flash / verify_ssl）。"""
    s = get_settings()
    # 与 services/llm_client.py:_create_chat_openai 保持一致的 SSL 处理
    http_client = httpx.Client(verify=s.httpx_verify_ssl)
    http_async_client = httpx.AsyncClient(verify=s.httpx_verify_ssl)
    extra_body = {"enable_thinking": True} if s.llm_enable_thinking else None
    return ChatOpenAI(
        api_key=s.llm_api_key,
        base_url=s.llm_base_url,
        model=s.llm_model,
        temperature=0.1,
        max_tokens=1024,
        http_client=http_client,
        http_async_client=http_async_client,
        extra_body=extra_body,
    )


def main() -> None:
    llm = build_poc_llm()
    agent = create_deep_agent(
        model=llm,
        tools=[add],
        system_prompt="You are a POC assistant. Use the `add` tool for any arithmetic.",
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "用 add 工具算 3 + 5，然后用中文解释结果。"}]}
    )
    last = result["messages"][-1]
    print("=== 最终回答 ===")
    print(last.content)
    print("=== 消息链（含 tool_call / tool_result）===")
    for m in result["messages"]:
        print(f"[{m.type}] {getattr(m, 'content', '')[:120]}")


if __name__ == "__main__":
    main()
```

**验证点**：脚本能否 import 通过、`create_deep_agent` 能否编译出 graph。**不**调中转站也能跑通前半段（可临时 mock llm 验证编译）。

### 第 2 步：纯 LLM 调用（验证轴 B 基线连通）

先不挂 tool，直接用 `build_poc_llm()` 发一条 `invoke("说一句你好")`，确认：

- 中转站能回（轴 B 基线）—— v1 已验证，此处复测确认 POC 脚本环境变量加载正确
- 无 SSL 报错（`HTTPX_VERIFY_SSL=false` 生效）

失败排查路径：`python/.env` 是否被加载（`get_settings()` 读的是环境变量，仓库根 `.env` → `python/.env`，后者覆盖前者，见 CLAUDE.md 陷阱）。

### 第 3 步：带 tool 的 ReAct 循环（验证轴 B tool-calling 双向兼容）

跑第 1 步的 `main()` 完整流程。重点看消息链里是否出现：

1. AI message 含 `tool_calls`（agent 决定调 `add`）
2. Tool message 含 `8`（工具执行结果回传）
3. 最终 AI message 回答中包含 `8`（agent 读回结果）

**若仅 1 出现、2/3 缺失** → 中转站 tool-calling 单向，属轴 B 失败，触发回退评估。
**若 1 即缺失**（agent 直接瞎答 8）→ 检查 `system_prompt` 与 `bind_tools` 是否生效；也可能是 `enable_thinking` 干扰 tool-calling。

### 第 4 步：thinking 与 tool-calling 冲突对照（可选但推荐）

中转站默认 `ECOM_LLM_ENABLE_THINKING=true`（`extra_body={"enable_thinking": True}`）。部分模型在 thinking 模式下 tool-calling 行为不稳定。对照实验：

- 4a：`enable_thinking=True` + tool → 观察是否正常
- 4b：`enable_thinking=False` + tool → 观察是否正常

**判定**：若仅 4a 异常而 4b 正常，则**不触发回退**，记录为「deepagents + 中转站需关闭 thinking」约束，写入 Phase 1 注意事项。若两者都异常，触发回退。

### 第 5 步：Docker 内跑通（验证轴 C）

POC 脚本放进镜像后，在容器内执行。由于 `Dockerfile` 构建时 `COPY . .`，新增 `scripts/poc_deepagents.py` 后**必须 `--build`**（CLAUDE.md 陷阱）：

```bash
# 1) 重建镜像（带上新脚本与新依赖）
docker compose -f docker-compose.python.yml --profile python up -d --build python-api

# 2) 容器内跑 POC（绕过 FastAPI，直接 python 执行）
docker compose -f docker-compose.python.yml --profile python exec python-api \
  python scripts/poc_deepagents.py
```

**验证点**：

- 镜像构建层能装上 `deepagents>=0.1.0` 及其传递依赖（requirements.txt 已加，见 §5）
- 容器内 `python/.env` 被正确注入（`env_file: ./python/.env`，docker-compose.python.yml 已配）
- 容器能出网到 `one.zhique.cn`（v1 已验证 LLM 可用，此处复测）

**常见坑**：若容器内 DNS/代理出不去中转站，对比 v1 `/health` 的 LLM 探活是否同样失败——若 v1 也失败则是网络层问题，非 deepagents 问题。

### 第 6 步：v1 回归（验证不破坏现状）

deepagents 引入会拉新版本的 `langgraph`/`langchain` 传递依赖。确认 v1 链路未被破坏：

```bash
docker compose -f docker-compose.python.yml --profile python exec python-api \
  python -m pytest tests/ -m "not slow" -v
```

重点关注 `test_supervisor_pipeline.py`、`test_stream_recommend.py` 是否仍绿。若红，定位是否为 langgraph 版本漂移导致，必要时在 `requirements.txt` 锁定版本范围（见 §5）。

---

## 4. go/no-go 判定矩阵

| 试金石 | 通过条件 | 状态 |
|--------|---------|------|
| 轴 A 导入 | 第 1 步 `import deepagents` + `create_deep_agent` 编译成功 | ✅ 通过（0.6.12，`CompiledStateGraph`，含 TodoListMiddleware） |
| 轴 B 基线 | 第 2 步纯 LLM 调用返回正常，无 SSL 报错 | ✅ 通过（"你好，我是DeepSeek"） |
| 轴 B tool-calling | 第 3 步消息链含 tool_call → tool_result(8) → 最终回答引用 8 | ✅ 通过（tool_call→8.0→"3+5=8"） |
| 轴 C Docker | 第 5 步容器内 POC 跑通，构建层依赖装得上 | ✅ 通过（DaoCloud 镜像源构建，deepagents 0.6.12 装入镜像） |
| v1 回归 | 第 6 步 `tests/ -m "not slow"` 全绿 | ⚠️ 44 通过 / 3 失败——**3 失败为预先存在的 A/B 路由问题，非 langchain 1.x 回归**（见 §4.1） |

### 4.1 v1 回归失败分析（结论：非 Phase 0 回归）

3 个失败均在 `test_supervisor_pipeline.py`：

| 测试 | 失败原因 |
|------|---------|
| `test_supervisor_filters_time_conflict_and_returns_course_reasons` | `assert 'course_recall' in response.agent_results` → `agent_results={}` |
| `test_supervisor_pipeline_uses_cached_recall_candidates` | `KeyError: 'course_recall'`（同上） |
| `test_supervisor_hard_constraint_filter_blocks_non_west_campus_courses` | 期望 `['GXK201']`，实际 `['GXK202','GXK201']` |

**根因**：测试用 `_AgentStub` 桩了 Pipeline agents 并期望 `agent_results` 被填充，但 `user_id` S10001/S10002/S10003 经 `ab_engine.assign(uid,'react_vs_pipeline')` 哈希全部落到 **react 组**（已实地验证），走 ReAct 路径（不使用桩 agents）→ `agent_results={}`。

**为何不是 Phase 0 回归**：
1. `git diff --stat HEAD` 证实本次会话未改动 `ab_test.py`/`supervisor.py`/`requirements.txt`/测试文件——失败在 HEAD 即存在
2. `ab_engine.assign` 是 `hashlib.sha256(user_id)` 纯 Python，与 langchain 版本无关
3. ReAct 路径在 langchain 1.x 下**正常执行**（日志 `react_complete rounds=9`、课程正常返回），非 API 崩溃
4. v1 核心导入（`config`/`llm_client`/`base_agent`/`supervisor`）在 1.x 下全部 OK
5. A/B 默认实验 `react_vs_pipeline` 在提交 `472935d` 设定，早于本次会话

**结论**：langchain 0.3→1.3.14 / langgraph 0.4→1.2.9 升级**未破坏 v1**。3 个失败是预先存在的测试设计问题（测试假设 pipeline 路径，未 mock `ab_engine` 强制分组），应作为独立技术债处理（在测试中 mock `ab_engine.assign` 强制 pipeline 组），**不阻塞 Phase 0**。

**判定**：

- **全部通过 → GO**：轴 A/B/C 全绿；v1 回归的 3 失败经分析为预先存在、非依赖升级回归。**Phase 0 GO**。
- 在本文件标 ✅，更新 `../plan.md` Phase 0 状态，进入 Phase 1。
- 3 个预存失败记为技术债，Phase 1 顺手修（mock ab_engine 强制 pipeline 组）。

---

## 5. 依赖与环境

### 5.1 requirements.txt（已就绪，无需改）

v2.0.0 依赖块已加（`python/requirements.txt`）：

```
deepagents>=0.1.0          # 决策3
langchain-mcp-adapters>=0.1.0  # 决策8/6b（POC 不用，但已随包安装）
langchain-openai>=0.3.0    # v1 已有，ChatOpenAI 入口
langgraph>=0.4.0           # v1 已有，deepagents 底座
```

POC 只用到 `deepagents` + `langchain-openai` + `langgraph`。`langchain-mcp-adapters` 等 Phase 1+ 才用，POC 阶段若构建层装它有困难，可临时注释掉——但**不建议**，因 Phase 1 立刻就要，早暴露早解决。

### 5.2 环境变量（复用 python/.env，无需新增）

POC 脚本经 `from config import get_settings` 复用 v1 全部中转站配置：

| 变量 | 值（python/.env） | 用途 |
|------|------------------|------|
| `LLM_API_KEY` | `sk-***` | 中转站鉴权 |
| `LLM_BASE_URL` | `https://one.zhique.cn/v1` | OpenAI 兼容端点 |
| `LLM_MODEL` | `deepseek-v4.1-flash` | 主模型 |
| `LLM_ENABLE_THINKING` | `true` | `extra_body`，第 4 步对照关掉 |
| `HTTPX_VERIFY_SSL` | `false` | 中转站证书 SAN 不匹配，必须关 |

> ⚠️ `.env.example` 仍是旧 MaaS 配置（`...maas.aliyuncs.com/compatible-mode/v1`），与实际 `python/.env`（`one.zhique.cn`）不一致。POC 以 `python/.env` 为准——Docker 也只注入 `python/.env`（CLAUDE.md 陷阱）。此偏差不在 Phase 0 范围，建议 Phase 1 顺手修 `.env.example`。

### 5.3 文件清单

| 路径 | 动作 | 说明 |
|------|------|------|
| `python/scripts/poc_deepagents.py` | **新增** | POC 主脚本（§3 第 1 步） |
| `python/requirements.txt` | 不改 | 依赖已就绪 |
| `python/.env` | 不改 | 复用现有中转站配置 |
| `docker-compose.python.yml` | 不改 | `--profile python` 已含 python-api |
| `python/Dockerfile` | 不改 | `COPY . .` 会带上新脚本，需 `--build` |
| v1 任何代码 | **不动** | 决策 4：v1 内部零改动 |

---

## 6. 回退决策（NO-GO 时）

若 §4 判定为 NO-GO（轴 A/B 不可绕过失败），按决策 2 备选执行回退。回退选择需在失败后单独开会决定，POC 阶段只列候选：

| 候选 | 做法 | 代价 | 保留投资 |
|------|------|------|---------|
| **备选 1：LangGraph 混合**（决策 2 原选项 A） | 退回纯 LangGraph `create_react_agent` + StateGraph 脊柱，自建文件系统/TodoWrite | 失去 deepagents 内置文件系统+subagent，需自实现 | LangGraph + v1 全保留 |
| **备选 2：OpenAI Agents SDK**（决策 2/3 原选项 B） | 迁移到官方 SDK，handoffs/guardrails/MCP | 无文件系统（决策 3 已论证）；有 TS 变体但 Python 端文件系统缺失影响报告场景 | 中转站 ChatOpenAI 兼容性需重验 |

**回退触发后的动作**：

1. 在本文件记录失败现象、定位的轴、根因证据（日志/报错）
2. 开会选定备选 1 或 2
3. 更新 `../plan.md` 决策 2/3 状态，重写 Phase 0 为对应备选的 POC
4. 同步 `notes/` 决策变更

> 注：若仅是 `enable_thinking` 与 tool-calling 冲突（第 4 步），属可绕过约束，**不算 NO-GO**，记录约束即可。

---

## 7. deepagents API 速记（第 0 步核对结果 · 2026-07-29 实地核实）

> 已在本地 venv 实地核对（`python -c "import deepagents; ..."` + `inspect.signature`）。

**包版本**：`deepagents 0.6.12`（远高于 `requirements.txt` 的 `>=0.1.0` 松-pin；松-pin 可装上最新版，Docker 构建应一致）。

**入口**：

```python
from deepagents import create_deep_agent
```

**签名**（关键字参数摘录）：

```python
create_deep_agent(
    model,              # BaseChatModel 实例可直接传入（我们的 ChatOpenAI 可用）；也接受 "provider:model" 字符串
    tools,              # Sequence[BaseTool | Callable | dict]；与内置工具合并
    *,
    system_prompt,      # str | SystemMessage | None
    middleware,         # AgentMiddleware 序列
    subagents,          # SubAgent | CompiledSubAgent 序列（决策 11 subagent 隔离用）
    skills, memory, permissions,
    backend,            # SandboxBackendProtocol；非沙箱 backend 的 execute 工具返回错误（不崩溃）
    checkpointer,       # BaseCheckpointSaver（决策 12 checkpointing 入口）
    store, debug, name, cache,
    response_format, state_schema, context_schema, interrupt_on,
) -> CompiledStateGraph
```

**默认内置工具**（印证决策 3/11「文件系统 + TodoWrite 内置」）：
- `write_todos`（TodoWrite 规划）
- `ls` / `read_file` / `write_file` / `edit_file` / `glob` / `grep`（虚拟文件系统）
- `execute`（shell；需 sandbox backend，否则返回错误信息）
- `task`（subagent 委派）

POC 传入的 `add` tool 与上述内置工具**合并**，agent 仍可正常调用 `add`——内置工具存在不破坏 POC。

**依赖版本（venv 实际）**：

| 包 | venv 实际 | requirements.txt pin | deepagents 要求 |
|----|----------|---------------------|----------------|
| langgraph | 1.2.9 | `>=0.4.0` | — |
| langchain | 1.3.14 | `>=0.3.0` | `>=1.3.11` |
| langchain-core | 1.5.1 | — | `>=1.4.8` |
| langchain-openai | 1.2.1 | `>=0.3.0` | — |
| deepagents | 0.6.12 | `>=0.1.0` | — |
| langchain-mcp-adapters | **未安装** | `>=0.1.0` | — |

**关键发现**：
1. deepagents 把 langchain 从 0.3 拉到 **1.3.14**、langgraph 拉到 **1.2.9**——v1 是按 0.3/0.4 写的，**v1 回归（第6步）是真实风险点**。已提前验证 v1 核心导入（`config`/`services.llm_client`/`agents.base_agent`/`orchestrator.supervisor`）在 1.x 下仍 OK，但完整测试套件需第6步验证。
2. `langchain-mcp-adapters` 在 venv **未安装**（requirements 列了但没装上）——Phase 0 不用 MCP，不影响 POC；Phase 1/3 需补装。
3. `model` 接受 `BaseChatModel` 实例 → POC 的 `build_poc_llm()` 返回的 `ChatOpenAI` 可直接传入，无需 `provider:model` 字符串。

**invoke 输入格式**（LangGraph 标准）：`agent.invoke({"messages": [{"role": "user", "content": "..."}]})`，返回 `{"messages": [...]}`。

---

## 8. 与总 plan / openspec 的衔接

- **本文件**：`docs/v2.0.0/notes/2026-07-29-phase-0-deepagents-poc详细计划.md`（用户指定放 `notes/`，与决策笔记同目录）
- **总 plan 引用**：`../plan.md` 第 45 行 `plans/phase-0-deepagents-poc.md（待生成）` 应更新指向本文件
- **openspec**：按决策 15「每阶段一个 OpenSpec change proposal」，Phase 0 通过后在 `openspec/changes/` 提 `2026-07-xx-phase-0-deepagents-poc`（含 `proposal.md` / `tasks.md` / `design.md`，结构参考 `changes/archive/2026-05-28-fix-category-fuzzy-match/`）。POC 本身是验证性质，spec 变更面很小，可轻量提。
- **后续 Phase**：GO 后生成 `phase-1-platform-base.md` 详细计划，再进 Phase 1 编码。

---

## 9. 执行 Checklist

- [x] 第 0 步：核对 deepagents API，填 §7（0.6.12，签名核实）
- [x] 第 1 步：写 `python/scripts/poc_deepagents.py`，本地 import + 编译通过
- [x] 第 2 步：纯 LLM 调用连通中转站
- [x] 第 3 步：带 tool 的 ReAct 循环，消息链三段齐全
- [x] 第 4 步：thinking on/off 对照（均兼容，无冲突）
- [x] 第 5 步：Docker `--build` 后容器内跑通（DaoCloud 镜像源）
- [x] 第 6 步：v1 `tests/ -m "not slow"` 回归（44 通过 / 3 预存失败，非回归，见 §4.1）
- [x] §4 判定矩阵全部勾选 → **GO**
- [x] 更新 `../plan.md` Phase 0 状态
- [ ] 提 openspec change proposal（GO 后，下一步）
- [ ] 技术债：3 个预存测试失败（mock `ab_engine` 强制 pipeline 组）——Phase 1 顺手修
