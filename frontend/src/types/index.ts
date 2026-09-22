export interface Course {
  course_id: string
  course_name: string
  teacher: string
  credits: number
  course_type: string
  course_category: string
  domain: string
  campus: string
  time_slot: string
  location: string
  capacity: number
  current_enrolled: number
  current_enrollment_ratio: number
  popularity_level: number
  rush_advice: string
  description: string
  assessment: string
  difficulty: string
  workload: string
  grade_friendly: string
  has_exam: number
  group_work_required: number
  suitable_for: string
  tags: string[]
  score: number
  match_reasons: string[]
}

export interface AgentResult {
  agent_name: string
  success: boolean
  latency_ms: number
  error: string | null
  data: Record<string, unknown>
  confidence: number
}

export interface RecommendationRequest {
  user_id: string
  scene?: string
  num_items?: number
  context?: Record<string, unknown>
  query?: string
  prompt?: string
  device_type?: string
  mode?: 'pipeline' | 'react'
}

export interface PriorityAdvice {
  advice: string
  priority: 'high' | 'medium' | 'low'
}

export interface RecommendationResponse {
  request_id: string
  user_id: string
  courses: Course[]
  recommendation_reasons: Array<Record<string, string>>
  selection_warnings: Array<Record<string, unknown>>
  priority_advice: Record<string, PriorityAdvice>
  experiment_group: string
  agent_results: Record<string, AgentResult>
  agent_latencies: Record<string, number>
  total_latency_ms: number
  timestamp: string
}

export interface HealthResponse {
  status: string
  model: string
  llm: {
    model: string
    base_url_host: string
    looks_like_dashscope: boolean
  }
  embedding_provider: string
  deps: {
    mysql: boolean
    redis: boolean
    milvus: boolean
  }
}

export interface ExperimentGroup {
  name: string
  weight: number
  config: Record<string, unknown>
  successes: number
  failures: number
}

export interface ExperimentInfo {
  name: string
  enabled: boolean
  groups: ExperimentGroup[]
  stats: Record<string, unknown>
}

export interface MetricsResponse {
  agents: Record<string, { total_calls: number; success_rate: number; avg_latency_ms: number }>
  business: Record<string, unknown>
}

export interface PresetQuery {
  id: string
  label: string
  icon: string
  prompt: string
}

// ── SSE Stream Events ──

export interface SSEPhaseData {
  phase: string
  request_id?: string
  num_items?: number
  profile_extracted?: boolean
  wide_recall_count?: number
  ranked_count?: number
  available_count?: number
  warning_count?: number
  final_count?: number
}

export interface SSECourseStartData {
  course_id: string
  course_name: string
  index: number
}

export interface SSECourseEndData {
  course_id: string
}

export interface SSETextData {
  course_id: string | null
  token: string
}

export interface SSEDoneData {
  request_id: string
  user_id: string
  courses: Course[]
  recommendation_reasons: Array<{ course_id: string; reason: string }>
  selection_warnings: Array<Record<string, unknown>>
  priority_advice: Record<string, PriorityAdvice>
  experiment_group: string
  agent_results: Record<string, AgentResult>
  total_latency_ms: number
}

export interface SSEErrorData {
  code: string
  message: string
  phase: string
  agent?: string
  request_id?: string
}

export type SSEEvent =
  | { event: 'phase'; data: SSEPhaseData }
  | { event: 'course_start'; data: SSECourseStartData }
  | { event: 'course_end'; data: SSECourseEndData }
  | { event: 'text'; data: SSETextData }
  | { event: 'done'; data: SSEDoneData }
  | { event: 'error'; data: SSEErrorData }

export interface StreamSegment {
  course_id: string | null
  course_name?: string
  tokens: string[]
}

export interface StreamDonePayload {
  request_id: string
  user_id: string
  courses: Course[]
  recommendation_reasons: Array<{ course_id: string; reason: string }>
  selection_warnings: Array<Record<string, unknown>>
  experiment_group: string
  agent_results: Record<string, AgentResult>
  total_latency_ms: number
}

// ── Chat SSE Events（/api/v1/chat/stream） ──

export interface ChatTextData {
  token: string
  session_id: string
}

export interface ChatToolData {
  tool: string
  status: 'start' | 'end'
  session_id: string
  tool_call_id?: string
  run_id?: string
  args?: Record<string, unknown>
  ok?: boolean
  code?: string
  message?: string
  retryable?: boolean
  latency_ms?: number | null
  result?: string
}

export interface AgentTreeNode {
  run_id: string
  name: string
  kind: 'main' | 'subagent'
  status: string
  args_summary?: string | null
  result_summary?: string | null
  latency_ms?: number | null
  children: AgentTreeNode[]
}

export interface ChatDoneData {
  reply: string
  messages_count: number
  session_id: string
  usage?: Record<string, unknown>
  latency_ms?: number | null
  ttft_ms?: number | null
  // Phase 4 E3：委派树契约
  agent_tree?: AgentTreeNode[]
}

export interface ChatErrorData {
  code: string
  message: string
  session_id: string
}

export type ChatEvent =
  | { event: 'text'; data: ChatTextData }
  | { event: 'tool'; data: ChatToolData }
  | { event: 'done'; data: ChatDoneData }
  | { event: 'error'; data: ChatErrorData }

// ── Report SSE Events（/api/v1/report） ──

export interface ReportStudentDoneData {
  student_id: string
  name: string
  status: string
  format: string
  url: string
}

export interface ReportStudentErrorData {
  student_id?: string
  name?: string
  reason?: string
  code?: string
}

export interface ReportDoneData {
  batch_id: string
  students: ReportStudentDoneData[]
  failed_students: ReportStudentErrorData[]
  warnings?: string[]
  summary?: string
}

export interface ReportErrorData {
  code: string
  message: string
}

/** report 上传批次（输入侧，GET /api/v1/report/batches）。 */
export interface ReportUploadBatch {
  batch_id: string
  user_id: string
  semester: string
  user_message: string
  file_count: number
  file_names: string[]
  status: 'processing' | 'done' | 'error'
  error_message?: string
  students_ok?: number
  students_failed?: number
  created_at?: string
}

/** GET /api/v1/report/batches 响应。 */
export interface ReportBatchesResult {
  count: number
  batches: ReportUploadBatch[]
}

/** report 批次详情：逐学生产物（GET /api/v1/report/batches/{batch_id}）。 */
export interface ReportArtifactDetail {
  id?: number
  batch_id: string
  student_id: string
  student_name: string
  format: string
  status: string
  file_key: string
  error_code?: string
  error_message?: string
  created_at?: string
  url?: string
}

/** GET /api/v1/report/batches/{batch_id} 响应。 */
export interface ReportBatchDetailResult {
  batch_id: string
  batch: ReportUploadBatch | null
  students: ReportArtifactDetail[]
}

export type ReportEvent =
  | { event: 'text'; data: { token: string } }
  | { event: 'tool'; data: { tool: string; status: string } }
  | { event: 'progress'; data: Record<string, unknown> }
  | { event: 'student_done'; data: ReportStudentDoneData }
  | { event: 'student_error'; data: ReportStudentErrorData }
  | { event: 'done'; data: ReportDoneData }
  | { event: 'error'; data: ReportErrorData }

// ── Evaluation SSE Events（/api/v1/evaluation） ──

export interface EvaluationRadarData {
  target_user_id: string
  dimensions: Array<{ name: string; metric: string; value: number; weight?: number }>
  rejected?: string[]
  overall_theme?: string
  status?: string
}

export interface EvaluationDoneData {
  evaluation_id: string
  target_user_id: string
  comment_type: string
  radar: EvaluationRadarData
  comment: string
  status: string
  comment_status: string
  usage?: Record<string, unknown>
}

export type EvaluationEvent =
  | { event: 'stage'; data: { stage: string; detail?: string } }
  | { event: 'radar'; data: EvaluationRadarData }
  | { event: 'comment_token'; data: { token: string } }
  | { event: 'done'; data: EvaluationDoneData }
  | { event: 'error'; data: ChatErrorData }

// ── Documents Upload（/api/v1/documents/upload，路 5：单/批统一） ──

/** 单文件摄入结果；status="ok" → 上传成功，否则失败。 */
export interface DocumentUploadDataset {
  /** 成功时为 uuid；失败时为 null。 */
  dataset_id: string | null
  /** 原文件名（重名时会被加 -1/-2 后缀）。 */
  filename?: string
  /** 文件字节大小。 */
  file_size?: number
  chunks_count: number
  status: 'ok' | 'completed' | 'error'
  /** 失败时的错误码（'file_too_large' / 异常类名等）。 */
  error?: string
  /** 失败时的可读消息。 */
  message?: string
  max_file_bytes?: number
  /** 2026-08-25：写入的 user_id 分区（'public' / 个人 user_id） */
  user_id?: string
}

/** 批量上传响应：count + datasets（每文件一份独立 dataset）。 */
export interface DocumentsUploadResult {
  count: number
  datasets: DocumentUploadDataset[]
}

/** 单条 dataset 列表项（来自 GET /api/v1/documents/datasets）。 */
export interface DocumentDataset {
  dataset_id: string
  dataset_name: string
  source_doc_name: string
  file_type: string
  chunks_count: number
  status: string
  user_id?: string
}

/** GET /api/v1/documents/datasets 响应。 */
export interface DocumentsListResult {
  count: number
  datasets: DocumentDataset[]
}

// ── 轻量认证（Phase 3.5） ──

export interface AuthUser {
  user_id: string
  name: string
  role: 'student' | 'teacher'
}

export interface AuthResponse {
  status: string
  token?: string
  user: AuthUser
}

// ── 会话管理（Phase 3.5） ──

export interface SessionInfo {
  session_id: string
  title: string
  display_title: string
  message_count: number
  created_at?: string
  updated_at?: string
}

export interface ChatImageAttachment {
  image_id: string
  mime_type: string
  width: number
  height: number
  file_size?: number
  preview_url: string
  filename?: string
}
export interface ChatHistoryMessage {
  seq: number
  role: string
  content: string | null
  tool_calls_json?: string | null
  attachments_json?: string | ChatImageAttachment[] | null
}
