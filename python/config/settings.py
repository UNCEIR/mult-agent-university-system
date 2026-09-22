from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 默认可在「仓库根」或「python/」下；仅依赖 cwd 的 ".env" 会导致在 python/ 里起服务时读不到根目录配置，
_PYTHON_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _PYTHON_ROOT.parent


def _env_file_candidates() -> tuple[str, ...] | str:
    paths: list[Path] = []
    for candidate in (_REPO_ROOT / ".env", _PYTHON_ROOT / ".env"):
        if candidate.is_file():
            paths.append(candidate)
    if paths:
        return tuple(str(p) for p in paths)
    return ".env"


class Settings(BaseSettings):
    app_name: str = "mult-agent-university-system"
    debug: bool = False
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = "deepseek-v4.1-flash"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_enable_thinking: bool = False

    redis_url: str = "redis://localhost:6379/0"
    feature_ttl_seconds: int = 86400
    # ── Phase 3 路 2：SSE Last-Event-ID 续传 ──
    # Redis 环形缓冲每个 thread_id 缓存的事件条数；超过会被 LTRIM 裁剪。
    # Redis 不可用时降级为不缓存（不影响正常生成）。
    sse_event_buffer_size: int = 100
    course_recall_cache_enabled: bool = True
    course_recall_cache_ttl_seconds: int = 900
    course_recall_cache_lock_ttl_seconds: int = 5
    course_recall_cache_wait_retries: int = 3
    course_recall_cache_wait_seconds: float = 0.1
    course_recall_cache_semantic_enabled: bool = True
    course_recall_cache_semantic_threshold: float = 0.95
    course_recall_cache_semantic_max_candidates: int = 12
    course_recall_cache_semantic_min_prompt_chars: int = 8

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "course_system"
    mysql_pool_size: int = 10
    mysql_max_overflow: int = 20

    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_user: str = ""
    milvus_password: str = ""
    milvus_uri: str = ""
    milvus_collection: str = "product_embeddings"
    course_milvus_collection: str = "course_chunks_real"
    document_milvus_collection: str = "document_chunks"
    milvus_dimension: int = 1024
    milvus_metric_type: str = "COSINE"
    milvus_index_type: str = "AUTOINDEX"

  
    embedding_provider: str = "openai"
    embedding_dimension: int = 1024
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-v4"
    embedding_batch_size: int = 8
    embedding_timeout_seconds: float = 30.0
    httpx_verify_ssl: bool = True

    # LLM 请求级超时（P0 修复）：不设置则沿用 openai SDK 默认值（数百秒量级），
    # 会绕过全部 agent_timeout_* / supervisor_global_timeout，表现为链路挂死且无任何日志。
    # 约束：llm_timeout_seconds 必须 < 对应 agent 的 timeout，否则 wait_for 会先触发，
    # 抛 asyncio.CancelledError（BaseException，except Exception 捕获不到）→ 取消路径静默。
    llm_timeout_seconds: float = 20.0  # 单次 LLM 请求总超时（覆盖 qwen 生成 2048 tokens 的正常耗时）
    llm_connect_timeout_seconds: float = 5.0  # 建连超时，快速失败
    llm_max_retries: int = 1  # 传输层重试次数（原为 openai SDK 默认 2）

    ab_test_enabled: bool = True
    ab_test_default_bucket_count: int = 100

    # 注意：agent 超时必须 > llm_timeout_seconds + 重试退避，否则 wait_for 会先于 LLM 请求
    # 超时触发，抛出 CancelledError（静默无日志）。profile = 单次 20s + 退避 + 1 次重试 ≈ 21~30s。
    agent_timeout_user_profile: float = 35.0
    agent_timeout_product_recall: float = 10.0
    agent_timeout_product_rerank: float = 15.0
    agent_timeout_marketing_copy: float = 20.0
    agent_timeout_inventory: float = 20.0

    # ── v2.0.0 主 Agent 记忆 / skill / checkpoint 配置 ─────────────────
    memory_dir: str = ""  # 长期记忆目录（AGENTS.md 所在目录，默认 <repo_root>/python/memories）
    skills_dir: str = ""  # skill 技能文档目录（默认 <repo_root>/python/skills）
    checkpoint_sqlite_path: str = ""  # SqliteSaver 持久路径（默认 <repo_root>/python/.checkpoint.db）
    checkpoint_backend: str = "sqlite"  # 决策 20：sqlite（默认，单实例）/ redis（仅实例数 > 1 时启用）

    # Phase 4：context_window 语义改为「缺省，被 model_catalog 覆盖」；get_model_meta(model) 优先
    agent_context_window_tokens: int = 128000  # 缺省上下文窗口（deepseek-v4.1-flash ≈ 128K）
    agent_compaction_trigger_tokens: int | None = None  # None 时用 context_window-13000
    agent_compaction_keep_tokens: int = 20000  # 决策 11: keepRecentTokens=20000
    agent_compaction_reserve_tokens: int = 16384  # Phase 4：窗口预留（window - reserve 触发压缩）
    agent_compaction_trigger_messages: int | None = 8  # demo 用 messages 触发（生产置 None 走 token 阈值）

    supervisor_max_retries: int = 2
    supervisor_global_timeout: float = 30.0
    stream_timeout_seconds: float = 60.0

    langchain_api_key: str = ""

    langchain_endpoint: str = "https://api.smith.langchain.com"
    langchain_tracing_v2: bool = True
    langchain_project: str = "mult-agent-university-system"

    # ── Phase 2：MinIO（report artifact 存储，本地兜底）─────────────
    minio_endpoint: str = "localhost"
    minio_port: int = 9002
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "12345678"
    minio_secure: bool = False
    minio_report_bucket: str = "report-artifacts"
    minio_connect_timeout: float = 3.0  # 探测/超时，超时自动走本地兜底

    # ── Phase 2：report（教师端批量成绩单）─────────────────────────
    report_download_ttl_hours: int = 24  # token 下载链接有效期
    report_download_secret: str = ""  # HMAC 签名密钥（.env 注入，空则禁用下载端点）
    report_max_file_mb: int = 10  # 单 Excel 上限
    report_max_files: int = 20  # 一次批量文件数上限
    report_render_concurrency: int = 4  # 渲染并发（WeasyPrint 同步：asyncio.to_thread + Semaphore）
    report_llm_fill_concurrency: int = 4  # LLM 填表/综合评语并发
    report_student_timeout_seconds: float = 60.0  # 单学生全链超时
    report_llm_fill_enabled: bool = True  # True=LLM 填表主路（慢，37 人约 12~15min）；False=确定性 Jinja2 直填（快 ~10x）
    report_stream_timeout_seconds: float = 600.0  # 整批报告生成死线（兜底，杜绝 SSE 无限挂起）

    # ── Phase 2：evaluation（教师端生成 → 学生端同步）──────────────
    evaluation_radar_axis_count: int = 5  # 雷达轴数（硬约束：维度提案必须恰为 N 个）

    # ── Phase 2：MCP 服务器注册表（三服务器占位）───────────────────
    # 格式：{"server_name": {"transport": "streamable_http", "url": "...", "api_key_env": "...", "namespace": "search"}}
    # pydantic-settings 从 .env 注入 dict 需 JSON 字符串（如 MCP_SERVERS='{"tavily":{...}}'）
    mcp_servers: dict = {}

    # ── Phase 2：视觉模型（image_recognize 直连，复用文本模型 key）──
    vision_model: str = "qwen3-vl-plus"  # 多模态选型（用户 2026-08-13 确认）
    vision_timeout_seconds: float = 60.0
    vision_connect_timeout_seconds: float = 5.0
    vision_max_retries: int = 1
    vision_max_tokens: int = 2048
    vision_temperature: float = 0.1
    vision_max_images: int = 4
    vision_max_image_bytes: int = 10 * 1024 * 1024
    vision_max_total_image_bytes: int = 30 * 1024 * 1024
    vision_max_pixels: int = 36_000_000
    vision_max_side: int = 2048
    vision_ttl_days: int = 30
    minio_chat_bucket: str = "chat-uploads"

    # ── Phase 2：插件工具 ────────────────────────────────────────────
    tavily_api_key: str = ""  # web_search 直连兜底（MCP 主路熔断时用）

    # ── Phase 2：即梦 4.0（火山引擎）图像生成 ────────────────────────
    volc_access_key: str = ""  # 火山引擎 AK（仅 .env，不入库）
    volc_secret_key: str = ""  # 火山引擎 SK（仅 .env，不入库）
    jimeng_req_key: str = "jimeng_t2i_v40"  # 即梦 4.0 服务标识
    jimeng_region: str = "cn-north-1"  # 签名 Region（固定）
    jimeng_service: str = "cv"  # 签名 Service（固定）
    jimeng_poll_interval_base: float = 3.0  # 轮询起始间隔（指数退避 3→6→10）
    jimeng_poll_interval_max: float = 10.0  # 轮询间隔封顶
    jimeng_poll_timeout: float = 120.0  # 总轮询超时（超时保留 task_id 可续查）
    jimeng_poll_max_attempts: int = 10  # 单次生成查询次数上限
    jimeng_scale_default: float = 0.7  # scale 默认值（文本遵从权重，eval 对照 0.5/0.7/0.9）

    # ── Phase 2：E2B 代码执行沙箱（e2b Python SDK 内核，自建 stdio MCP server）──
    e2b_api_key: str = ""  # E2B API key（仅 .env，不入库）
    e2b_sandbox_timeout: int = 300  # sandbox 保活 TTL 秒（每次执行后刷新）

    # ── Phase 2：chat 长期记忆（pi 机制移植）───────────────────────
    memory_extract_threshold_messages: int = 10  # 消息数达阈值触发跨会话记忆提取（≈5 轮；每轮 user+assistant 各算一条，可配）
    memory_extract_max_messages: int = 400  # 单批提取最大消息数（oldest-first 分批推进）
    memory_extract_retry_after_seconds: int = 600  # 提取失败退避间隔
    memory_extract_min_interval_seconds: int = 60  # 信号即时提取的同 user 最小间隔（0=关闭限频）
    memory_extract_disclosure_signal_enabled: bool = False  # 弱披露组信号开关（默认关，避免噪声）
    memory_entry_ttl_days: int = 30  # preference/decision 记忆 TTL（天）；fact 永不过期；0=不过期（到期仅不注入，consolidation 顺带清理）
    memory_entries_per_user_limit: int = 200  # 新会话注入的记忆条目上限（总字符 ≤2000）
    memory_consolidate_threshold_per_kind: int = 15  # 单 kind 记忆条目数超限触发 LLM 合并（consolidation）

    # ── Phase 3.5：轻量认证（HMAC token；业务接口维持 user_id 临时口径）──
    auth_token_secret: str = "campus-dev-secret"  # token 签名密钥（生产必须 .env 覆盖）
    auth_token_ttl_seconds: int = 7 * 24 * 3600  # token 有效期（默认 7 天）

    model_config = SettingsConfigDict(
        env_file=_env_file_candidates(),
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
