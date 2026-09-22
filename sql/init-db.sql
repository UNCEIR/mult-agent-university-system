-- ============================================================
-- init-db.sql — 公选课系统 MySQL 初始化
-- 库名: course_system (密码: 123456,见 docker-compose.yml)
-- 表清单:
--   course_records   — v1 课程结构化数据(500 门公选课)
--   course_chunks    — v1 课程文本块(每课 4 块:basic/schedule_capacity/learning_profile/audience_tags)
--   document_records — v2 文档摄入 dataset 级元数据(源文档)
--   document_chunks  — v2 文档摄入 chunk 级元数据(分块)
--   report_artifacts — v2 Phase2 report(教师端成绩单)产物元数据
--   report_uploads    — v2 report 上传批次元数据(输入侧，与知识库分表)
--   evaluation_records — v2 Phase2 evaluation(教师端生成→学生端同步)评价档案
--   chat_sessions/chat_messages/chat_memory_entries — v2 Phase2 chat 长期记忆
-- source of truth: 本文件是建表唯一来源,代码层不建表只 CRUD
-- 重建方式: docker compose down -v && docker compose up -d --build
-- 字符集: utf8mb4 / utf8mb4_unicode_ci
-- ============================================================

CREATE DATABASE IF NOT EXISTS course_system DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE course_system;

-- ------------------------------------------------------------ course_records
CREATE TABLE IF NOT EXISTS course_records (
    course_id VARCHAR(64) PRIMARY KEY,
    course_name VARCHAR(255) NOT NULL,
    teacher VARCHAR(128) DEFAULT '',
    credits DECIMAL(4,2) DEFAULT 0,
    course_type VARCHAR(64) DEFAULT '',
    course_category VARCHAR(128) DEFAULT '',
    domain VARCHAR(128) DEFAULT '',
    campus VARCHAR(64) DEFAULT '',
    time_slot VARCHAR(128) DEFAULT '',
    capacity INT DEFAULT 0,
    current_enrolled INT DEFAULT 0,
    popularity_level TINYINT DEFAULT 0,
    has_exam TINYINT DEFAULT 0,
    group_work_required TINYINT DEFAULT 0,
    tags TEXT,
    raw_json JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    search_text TEXT GENERATED ALWAYS AS (CONCAT_WS(' ', course_name, teacher, course_category, domain, campus, time_slot, tags)) STORED,
    FULLTEXT INDEX ft_search_text (search_text) WITH PARSER ngram,
    INDEX idx_domain (domain),
    INDEX idx_course_category (course_category),
    INDEX idx_campus (campus),
    INDEX idx_popularity_enrolled (popularity_level DESC, current_enrolled DESC, course_id ASC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ course_chunks
CREATE TABLE IF NOT EXISTS course_chunks (
    chunk_id VARCHAR(128) PRIMARY KEY,
    course_id VARCHAR(64) NOT NULL,
    chunk_index INT NOT NULL,
    chunk_type VARCHAR(64) NOT NULL,
    content TEXT NOT NULL,
    metadata_json JSON,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_course_chunks_course (course_id),
    INDEX idx_course_chunks_type (chunk_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ document_records
CREATE TABLE IF NOT EXISTS document_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    dataset_id VARCHAR(64) NOT NULL UNIQUE,
    dataset_name VARCHAR(255) NOT NULL,
    source_doc_name VARCHAR(512) NOT NULL,
    storage_path VARCHAR(1024) NOT NULL,
    file_type VARCHAR(16) NOT NULL,
    file_size BIGINT DEFAULT 0,
    chunk_strategy VARCHAR(32) DEFAULT 'auto',
    chunks_count INT DEFAULT 0,
    status VARCHAR(16) DEFAULT 'pending',
    error_message TEXT,
    -- 2026-08-25：document 归属用户；手册用 'public'，个人成绩单用 user_id
    -- 与 Milvus document_chunks.user_id 一致（同分区语义），过滤走 IN ['public', user_id]
    user_id VARCHAR(128) NOT NULL DEFAULT 'public',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_doc_records_dataset_name (dataset_name),
    INDEX idx_doc_records_status (status),
    INDEX idx_doc_records_user_id (user_id),
    INDEX idx_doc_records_created (created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2026-08-25：兼容已存在的 document_records（旧版没 user_id 列）；
-- 用 INFORMATION_SCHEMA 检查列存在性后再 ALTER（避开 MySQL 8.0 < 8.0.29 不支持
-- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 的兼容问题）。
-- 用 PREPARE/EXECUTE 纯 SQL 而非 DELIMITER（init-file 模式不识别 DELIMITER 指令）。
SET @col_exists := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'document_records'
      AND COLUMN_NAME = 'user_id'
);
SET @stmt := IF(
    @col_exists = 0,
    "ALTER TABLE document_records ADD COLUMN user_id VARCHAR(128) NOT NULL DEFAULT 'public' AFTER error_message, ADD INDEX idx_doc_records_user_id (user_id)",
    "DO 0"  -- no-op
);
PREPARE stmt FROM @stmt;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- ------------------------------------------------------------ document_chunks
CREATE TABLE IF NOT EXISTS document_chunks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    chunk_id VARCHAR(64) NOT NULL UNIQUE,
    dataset_id VARCHAR(64) NOT NULL,
    chunk_index INT NOT NULL,
    chunk_type VARCHAR(32) DEFAULT 'generic_fixed',
    content_preview VARCHAR(512) DEFAULT '',
    page_number INT DEFAULT 0,
    milvus_vector_id VARCHAR(128) DEFAULT '',
    content TEXT NOT NULL,
    metadata_json JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_doc_chunks_dataset (dataset_id),
    INDEX idx_doc_chunks_type (chunk_type),
    INDEX idx_doc_chunks_page (dataset_id, page_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ chat_attachments
-- 私有聊天图片资产：image_id 对外引用，实际对象在 MinIO/本地兜底；不允许进入 public images/ 命名空间
CREATE TABLE IF NOT EXISTS chat_attachments (
    attachment_id VARCHAR(40) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    file_key VARCHAR(512) NOT NULL,
    mime_type VARCHAR(64) NOT NULL,
    filename VARCHAR(255) NOT NULL DEFAULT '',
    file_size INT NOT NULL DEFAULT 0,
    width INT NOT NULL DEFAULT 0,
    height INT NOT NULL DEFAULT 0,
    sha256 CHAR(64) NOT NULL DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'ready',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at DATETIME NULL,
    INDEX idx_chat_attachments_user_session (user_id, session_id, created_at DESC),
    INDEX idx_chat_attachments_sha (sha256),
    INDEX idx_chat_attachments_expiry (status, expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 兼容已存在的 chat_attachments（旧版无 filename 列）
SET @col_chat_attachment_filename := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_attachments'
      AND COLUMN_NAME = 'filename'
);
SET @stmt_chat_attachment_filename := IF(
    @col_chat_attachment_filename = 0,
    "ALTER TABLE chat_attachments ADD COLUMN filename VARCHAR(255) NOT NULL DEFAULT '' AFTER mime_type",
    "DO 0"
);
PREPARE stmt_chat_attachment_filename FROM @stmt_chat_attachment_filename;
EXECUTE stmt_chat_attachment_filename;
DEALLOCATE PREPARE stmt_chat_attachment_filename;
-- 2026-09-21：兼容已存在的 document_chunks，增加中文 ngram 全文索引（不存在时才 ALTER）
SET @has_doc_chunks_ft := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'document_chunks'
      AND INDEX_NAME = 'ft_document_chunks_content'
);
SET @stmt_doc_chunks_ft := IF(
    @has_doc_chunks_ft = 0,
    "ALTER TABLE document_chunks ADD FULLTEXT INDEX ft_document_chunks_content (content) WITH PARSER ngram",
    "DO 0"
);
PREPARE stmt_doc_chunks_ft FROM @stmt_doc_chunks_ft;
EXECUTE stmt_doc_chunks_ft;
DEALLOCATE PREPARE stmt_doc_chunks_ft;

-- ------------------------------------------------------------ report_artifacts
-- report(教师端批量成绩单)产物元数据：一学生一行，支持失败重试/下载寻址/审计
CREATE TABLE IF NOT EXISTS report_artifacts (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(32) NOT NULL,
    student_id VARCHAR(64) NOT NULL,
    student_name VARCHAR(128) NOT NULL DEFAULT '',
    format VARCHAR(8) NOT NULL DEFAULT 'pdf',          -- pdf | html
    status VARCHAR(16) NOT NULL DEFAULT 'ok',           -- ok | failed
    file_key VARCHAR(512) NOT NULL DEFAULT '',          -- MinIO 对象键或本地相对路径
    token_expires_at DATETIME DEFAULT NULL,             -- 下载 token 过期时间（TTL 校验落点）
    error_code VARCHAR(64) DEFAULT '',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_report_artifacts_batch (batch_id),
    INDEX idx_report_artifacts_student (student_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- ------------------------------------------------------------ report_uploads
-- report 上传批次元数据（输入侧）：一次批量上传 = 一行。与 document_records（知识库）
-- 按业务分表不混存；与 report_artifacts（输出侧，逐学生产物）粒度不同——本表记录
-- 上传批次（来源 Excel 清单 + 归属 user_id + 状态机），artifacts 记录生成产物。
-- 状态机：processing（已落盘，管线运行中）→ done | error
CREATE TABLE IF NOT EXISTS report_uploads (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    batch_id VARCHAR(64) NOT NULL UNIQUE,                  -- 上传批次 id（rb_<hex>）
    merged_batch_id VARCHAR(64) NOT NULL DEFAULT '',       -- 工具合并批次 b_xxx（done 后回填，供详情反查）
    user_id VARCHAR(64) NOT NULL DEFAULT '',               -- 发起上传的教师 user_id（归属/列表过滤）
    semester VARCHAR(128) NOT NULL DEFAULT '',             -- 学期（可选）
    user_message TEXT,                                     -- 补充要求（可选，透传生成 Agent）
    file_count INT NOT NULL DEFAULT 0,                     -- 上传文件数
    file_names JSON,                                       -- 上传文件名清单（前端展示）
    status VARCHAR(16) NOT NULL DEFAULT 'processing',      -- processing | done | error
    error_message TEXT,                                    -- 失败原因（status=error 时）
    students_ok INT NOT NULL DEFAULT 0,                    -- 成功生成份数（done 回填）
    students_failed INT NOT NULL DEFAULT 0,                -- 失败份数（done 回填）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_report_uploads_user (user_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 兼容已存在的 report_uploads（旧版没有 merged_batch_id 列）：列存在性检查后 ALTER
SET @col_merged := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'report_uploads' AND COLUMN_NAME = 'merged_batch_id');
SET @stmt_merged := IF(@col_merged = 0, "ALTER TABLE report_uploads ADD COLUMN merged_batch_id VARCHAR(64) NOT NULL DEFAULT '' AFTER batch_id", "DO 0");
PREPARE stmt_merged FROM @stmt_merged; EXECUTE stmt_merged; DEALLOCATE PREPARE stmt_merged;



-- ------------------------------------------------------------ evaluation_records
-- evaluation(教师端生成→学生端同步)评价档案：append 保留历史，学生端只读本人
CREATE TABLE IF NOT EXISTS evaluation_records (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    target_user_id VARCHAR(64) NOT NULL,
    comment_type VARCHAR(32) NOT NULL,                 -- semester_summary|encouragement|improvement_advice|recommendation
    radar_json JSON,                                   -- 维度提案 + 确定性雷达值
    comment TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'generated',   -- generated | fallback
    generated_by VARCHAR(64) DEFAULT '',               -- 教师 user_id（临时口径，不校验）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_evaluation_user_time (target_user_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ chat_sessions
-- chat 会话元数据：会话归属/统计/记忆提取水位（last_extracted_seq 幂等标记）
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    message_count INT NOT NULL DEFAULT 0,
    last_extracted_seq INT NOT NULL DEFAULT 0,          -- 记忆提取水位（增量幂等）
    last_failure_at BIGINT NOT NULL DEFAULT 0,          -- 提取失败时间戳（epoch 秒，退避用）
    status VARCHAR(16) NOT NULL DEFAULT 'active',        -- active | closed
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_chat_sessions_user (user_id, updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------------ chat_messages
-- chat 会话记录（append-only）：用户当前通话的消息历史，可查询/可审计/是记忆提取源
CREATE TABLE IF NOT EXISTS chat_messages (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    seq INT NOT NULL,
    role VARCHAR(16) NOT NULL,                           -- user | assistant | tool
    content MEDIUMTEXT,
    tool_calls_json JSON,                                -- assistant 工具调用（审计）
    attachments_json JSON,                               -- 用户消息图片附件元数据（私有 image_id）
    usage_json JSON,                                     -- token 统计（Phase 4 指标源）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_chat_messages_session_seq (session_id, seq),
    INDEX idx_chat_messages_user (user_id, seq),
    INDEX idx_chat_messages_session (session_id, seq)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 2026-09-21：兼容已存在的 chat_messages（旧版无 attachments_json 列）
SET @col_chat_attachments := (
    SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_messages'
      AND COLUMN_NAME = 'attachments_json'
);
SET @stmt_chat_attachments := IF(
    @col_chat_attachments = 0,
    "ALTER TABLE chat_messages ADD COLUMN attachments_json JSON NULL AFTER tool_calls_json",
    "DO 0"
);
PREPARE stmt_chat_attachments FROM @stmt_chat_attachments;
EXECUTE stmt_chat_attachments;
DEALLOCATE PREPARE stmt_chat_attachments;
-- ------------------------------------------------------------ chat_memory_entries
-- 跨会话长期记忆：按 user_id 隔离，新会话首轮注入；AGENTS.md 不再承载用户级内容
CREATE TABLE IF NOT EXISTS chat_memory_entries (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    agent_name VARCHAR(64) NOT NULL DEFAULT 'main_agent',  -- Phase 4 D6：每 agent 独立记忆点
    kind VARCHAR(16) NOT NULL DEFAULT 'fact',            -- preference | fact | decision
    content TEXT NOT NULL,
    content_hash CHAR(32) NOT NULL,                      -- NFKC 归一后 md5（精确去重键）
    source_session_id VARCHAR(64) NOT NULL DEFAULT '',
    expires_at DATETIME NULL,                        -- 记忆过期时间（NULL=永不过期；preference/decision 默认 30 天 TTL）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_memory_dedup (user_id, agent_name, kind, content_hash),
    INDEX idx_memory_entries_user (user_id, agent_name, updated_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
-- 兼容旧库（无 agent_name 列 / 旧唯一键）：列存在性 + 索引存在性守卫后迁移
SET @col_agent_name := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_memory_entries' AND COLUMN_NAME = 'agent_name');
SET @stmt_add_agent := IF(@col_agent_name = 0, "ALTER TABLE chat_memory_entries ADD COLUMN agent_name VARCHAR(64) NOT NULL DEFAULT 'main_agent' AFTER user_id", "DO 0");
PREPARE stmt_add_agent FROM @stmt_add_agent; EXECUTE stmt_add_agent; DEALLOCATE PREPARE stmt_add_agent;
SET @has_old_uniq := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_memory_entries' AND INDEX_NAME = 'uq_memory_dedup' AND COLUMN_NAME = 'content_hash' AND NOT EXISTS (
        SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS s2
        WHERE s2.TABLE_SCHEMA = DATABASE() AND s2.TABLE_NAME = 'chat_memory_entries' AND s2.INDEX_NAME = 'uq_memory_dedup' AND s2.COLUMN_NAME = 'agent_name'));
SET @stmt_drop_uniq := IF(@has_old_uniq > 0, "ALTER TABLE chat_memory_entries DROP INDEX uq_memory_dedup", "DO 0");
PREPARE stmt_drop_uniq FROM @stmt_drop_uniq; EXECUTE stmt_drop_uniq; DEALLOCATE PREPARE stmt_drop_uniq;
SET @has_new_uniq := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_memory_entries' AND INDEX_NAME = 'uq_memory_dedup' AND COLUMN_NAME = 'agent_name');
SET @stmt_add_uniq := IF(@has_new_uniq = 0, "ALTER TABLE chat_memory_entries ADD UNIQUE KEY uq_memory_dedup (user_id, agent_name, kind, content_hash)", "DO 0");
PREPARE stmt_add_uniq FROM @stmt_add_uniq; EXECUTE stmt_add_uniq; DEALLOCATE PREPARE stmt_add_uniq;
-- 兼容旧库（无 expires_at 列）：列存在性守卫后迁移
SET @col_expires := (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_memory_entries' AND COLUMN_NAME = 'expires_at');
SET @stmt_add_exp := IF(@col_expires = 0, "ALTER TABLE chat_memory_entries ADD COLUMN expires_at DATETIME NULL AFTER source_session_id", "DO 0");
PREPARE stmt_add_exp FROM @stmt_add_exp; EXECUTE stmt_add_exp; DEALLOCATE PREPARE stmt_add_exp;

-- ------------------------------------------------------------ users
-- 轻量认证用户表（Phase 3.5）：学号/工号唯一；角色 student | teacher
-- 密码 sha256(盐 + 密码)；token 由 api/auth.py 签发（HMAC），业务接口维持 user_id 临时口径
CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL DEFAULT 'student',         -- student | teacher
    password_hash VARCHAR(128) NOT NULL,
    salt VARCHAR(32) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;


-- ------------------------------------------------------------ chat_session_compactions
-- 会话压缩摘要落库（Phase 4 P0-A）：短期 checkpoint 塞满时 compact 汇总，自包含行。
-- 业务闭环：SummarizationSyncMiddleware 触发压缩 → 写后同步落库 → 读路径注入上下文。
CREATE TABLE IF NOT EXISTS chat_session_compactions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    summary MEDIUMTEXT NOT NULL,                          -- 压缩摘要（双模板：首轮六节 / 增量 update）
    prev_compaction_id BIGINT NULL,                       -- 上一次压缩行 id（增量合并链）
    first_kept_message_id BIGINT NOT NULL DEFAULT 0,      -- 保留的首条消息 seq（续读水位）
    tokens_before INT NOT NULL DEFAULT 0,                 -- 压缩前 token 估算
    tokens_after INT NOT NULL DEFAULT 0,                  -- 压缩后 token 估算
    reserve_tokens INT NOT NULL DEFAULT 0,                -- 预留窗口（window-reserve 决策用）
    keep_recent_tokens INT NOT NULL DEFAULT 0,            -- 保留最近 token 数
    model VARCHAR(64) NOT NULL DEFAULT '',                -- 触发时模型（catalog 查询）
    reason VARCHAR(16) NOT NULL DEFAULT 'threshold',      -- threshold | overflow | manual | fallback
    status VARCHAR(16) NOT NULL DEFAULT 'ok',             -- ok | failed | aborted | fallback
    usage_json JSON NULL,                                 -- 压缩 LLM 调用 usage（成本记账）
    details_json JSON NULL,                               -- 扩展细节（引用 ID 清单等，Phase 4 预留）
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_compactions_session (user_id, session_id, created_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
