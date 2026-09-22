import type {
  AuthResponse,
  ChatEvent,
  ChatHistoryMessage,
  ChatImageAttachment,
  DocumentsListResult,
  DocumentsUploadResult,
  EvaluationEvent,
  HealthResponse,
  MetricsResponse,
  ExperimentInfo,
  RecommendationRequest,
  RecommendationResponse,
  ReportBatchDetailResult,
  ReportBatchesResult,
  ReportEvent,
  SessionInfo,
  SSEEvent,
} from '../types'
import { consumeSSE, consumeSSEWithRetry, type ConsumeSSEOptions } from './sse'

const API_BASE = '/api/v1'

/** 解包统一响应信封（BaseResult {code, success, data, msg}）：成功取 data，非信封原样返回。 */
export function unwrapEnvelope<T>(body: unknown): T {
  if (
    body &&
    typeof body === 'object' &&
    'code' in body &&
    'success' in body &&
    'data' in body &&
    'msg' in body
  ) {
    return (body as { data: T }).data
  }
  return body as T
}

/** 从统一信封 / FastAPI detail / 状态文本中提取错误消息。 */
function errorMessage(body: unknown, statusText: string): string {
  if (body && typeof body === 'object') {
    const b = body as Record<string, unknown>
    if (typeof b.msg === 'string' && b.msg) return b.msg
    if (typeof b.detail === 'string' && b.detail) return b.detail
  }
  return statusText
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(errorMessage(body, res.statusText))
  }
  return unwrapEnvelope<T>(body)
}

async function getHealth(): Promise<HealthResponse> {
  const res = await fetch('/health', { cache: 'no-store' })
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`)
  // /health 后端返回统一信封 {code, success, data, msg}，与其它 /api/v1 端点一致
  return unwrapEnvelope<HealthResponse>(await res.json())
}

interface ChatRequestBody {
  message: string
  session_id: string
  user_id: string
  image_ids?: string[]
  images?: string[]
}

export const api = {
  health: getHealth,

  // ── 轻量认证（Phase 3.5） ──
  register: (body: { user_id: string; name: string; role: string; password: string }) =>
    request<AuthResponse>('/auth/register', { method: 'POST', body: JSON.stringify(body) }),

  login: (body: { user_id: string; password: string }) =>
    request<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),

  // ── 会话管理（Phase 3.5） ──
  listSessions: (userId: string) =>
    request<{ sessions: SessionInfo[] }>(`/chat/sessions?user_id=${encodeURIComponent(userId)}`),

  sessionMessages: (sessionId: string, userId: string) =>
    request<{ session_id: string; messages: ChatHistoryMessage[] }>(
      `/chat/sessions/${encodeURIComponent(sessionId)}/messages?user_id=${encodeURIComponent(userId)}`,
    ),

  renameSession: (sessionId: string, userId: string, title: string) =>
    request<{ status: string }>(
      `/chat/sessions/${encodeURIComponent(sessionId)}/rename?user_id=${encodeURIComponent(userId)}`,
      {
        method: 'POST',
        body: JSON.stringify({ title }),
      },
    ),

  uploadChatImages: async (
    files: File[],
    sessionId: string,
    userId: string,
    signal?: AbortSignal,
  ): Promise<{ images: ChatImageAttachment[] }> => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    form.append('session_id', sessionId)
    form.append('user_id', userId)
    const res = await fetch(`${API_BASE}/chat/images/upload`, { method: 'POST', body: form, signal })
    const body = await res.json().catch(() => null)
    if (!res.ok) throw new Error(errorMessage(body, res.statusText))
    return unwrapEnvelope<{ images: ChatImageAttachment[] }>(body)
  },
  deleteChatImage: (imageId: string, userId: string, sessionId: string) =>
    request<{ status: string; image_id: string }>(
      `/chat/images/${encodeURIComponent(imageId)}?user_id=${encodeURIComponent(userId)}&session_id=${encodeURIComponent(sessionId)}`,
      { method: 'DELETE' },
    ),
  closeSession: (sessionId: string, userId: string) =>
    request<{ status: string }>(
      `/chat/sessions/${encodeURIComponent(sessionId)}?user_id=${encodeURIComponent(userId)}`,
      {
        method: 'DELETE',
      },
    ),

  getExperiments: () => request<Record<string, ExperimentInfo>>('/experiments'),

  getMetrics: () => request<MetricsResponse>('/api/v1/metrics'),

  /** Phase 4 C2：/metrics Prometheus 文本（不走信封，供抓取/调试）。 */
  async getPrometheusText(): Promise<string> {
    const res = await fetch('/metrics')
    if (!res.ok) throw new Error(`prometheus fetch failed: ${res.status}`)
    return res.text()
  },

  recordOutcome: (experimentId: string, group: string, success: boolean) =>
    request<{ status: string }>(
      `/experiments/${experimentId}/outcome?group=${group}&success=${success}`,
      {
        method: 'POST',
      },
    ),

  // ── 推荐（统一流式入口，mode=pipeline|react） ──
  async *recommendStream(
    body: RecommendationRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<SSEEvent> {
    for await (const evt of consumeSSE(
      `${API_BASE}/recommend/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
    )) {
      yield evt as SSEEvent
    }
  },

  /** 路 2：带指数退避重连 + Last-Event-ID 续传的推荐流（默认 3 次重试）。 */
  async *recommendStreamWithRetry(
    body: RecommendationRequest,
    signal?: AbortSignal,
    options?: ConsumeSSEOptions,
  ): AsyncGenerator<SSEEvent> {
    for await (const evt of consumeSSEWithRetry(
      `${API_BASE}/recommend/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
      options,
    )) {
      yield evt as SSEEvent
    }
  },

  // ── 智能对话 ──
  chat: (body: ChatRequestBody) =>
    request<{
      reply: string
      session_id: string
      messages_count: number
      usage: Record<string, unknown>
      latency_ms: number | null
    }>('/chat', { method: 'POST', body: JSON.stringify(body) }),

  async *chatStream(body: ChatRequestBody, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
    for await (const evt of consumeSSE(
      `${API_BASE}/chat/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
    )) {
      yield evt as ChatEvent
    }
  },

  /** 路 2：带指数退避重连 + Last-Event-ID 续传的对话流。 */
  async *chatStreamWithRetry(
    body: ChatRequestBody,
    signal?: AbortSignal,
    options?: ConsumeSSEOptions,
  ): AsyncGenerator<ChatEvent> {
    for await (const evt of consumeSSEWithRetry(
      `${API_BASE}/chat/stream`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
      options,
    )) {
      yield evt as ChatEvent
    }
  },

  // ── 成绩报告 ──
  async *reportUpload(
    files: File[],
    semester: string,
    className: string,
    userMessage: string,
    userId: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ReportEvent> {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    form.append('semester', semester)
    form.append('class_name', className)
    form.append('user_message', userMessage)
    if (userId) form.append('user_id', userId)
    for await (const evt of consumeSSE(
      `${API_BASE}/report`,
      { method: 'POST', body: form },
      signal,
    )) {
      yield evt as ReportEvent
    }
  },

  /** 路 2：带指数退避重连的报告上传（断点续传需配合 X-SSE-Thread-Key header）。 */
  async *reportUploadWithRetry(
    files: File[],
    semester: string,
    className: string,
    userMessage: string,
    userId: string,
    signal?: AbortSignal,
    options?: ConsumeSSEOptions & { threadKey?: string },
  ): AsyncGenerator<ReportEvent> {
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    form.append('semester', semester)
    form.append('class_name', className)
    form.append('user_message', userMessage)
    if (userId) form.append('user_id', userId)
    const extraHeaders: Record<string, string> = {}
    if (options?.threadKey) extraHeaders['X-SSE-Thread-Key'] = options.threadKey
    for await (const evt of consumeSSEWithRetry(
      `${API_BASE}/report`,
      { method: 'POST', body: form },
      signal,
      { ...options, extraHeaders },
    )) {
      yield evt as ReportEvent
    }
  },

  // ── 评价寄语 ──
  async *evaluation(
    body: { target_user_id: string; comment_type: string; generated_by?: string },
    signal?: AbortSignal,
  ): AsyncGenerator<EvaluationEvent> {
    for await (const evt of consumeSSE(
      `${API_BASE}/evaluation`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
    )) {
      yield evt as EvaluationEvent
    }
  },

  /** 路 2：带指数退避重连 + Last-Event-ID 续传的评价流。 */
  async *evaluationWithRetry(
    body: { target_user_id: string; comment_type: string; generated_by?: string },
    signal?: AbortSignal,
    options?: ConsumeSSEOptions,
  ): AsyncGenerator<EvaluationEvent> {
    for await (const evt of consumeSSEWithRetry(
      `${API_BASE}/evaluation`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
      signal,
      options,
    )) {
      yield evt as EvaluationEvent
    }
  },

  /** 2026-08-31：ReportPage「已生成批次」列表（输入侧上传记录，与 documentsList 对齐）。 */
  async reportBatches(userId: string, signal?: AbortSignal): Promise<ReportBatchesResult> {
    if (!userId) throw new Error('请先登录后再查看报告记录')
    const res = await fetch(`${API_BASE}/report/batches?user_id=${encodeURIComponent(userId)}`, {
      signal,
    })
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      throw new Error(errorMessage(err, res.statusText))
    }
    return unwrapEnvelope(await res.json())
  },

  /** 2026-08-31：批次详情（上传记录 + 逐学生产物，含 token 下载/预览 URL）。 */
  async reportBatchDetail(
    batchId: string,
    userId: string,
    signal?: AbortSignal,
  ): Promise<ReportBatchDetailResult> {
    if (!userId) throw new Error('请先登录后再查看报告详情')
    const res = await fetch(
      `${API_BASE}/report/batches/${encodeURIComponent(batchId)}?user_id=${encodeURIComponent(userId)}`,
      { signal },
    )
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      throw new Error(errorMessage(err, res.statusText))
    }
    return unwrapEnvelope(await res.json())
  },

  evaluationMe: (userId: string) =>
    request<{
      user_id: string
      items: Array<Record<string, unknown>>
    }>(`/evaluation/me?user_id=${encodeURIComponent(userId)}`),

  // ── 知识库上传（单/批统一：files: File[]；后端 max_length=5） ──
  async documentsUpload(
    files: File[],
    datasetName: string,
    chunkStrategy: string,
    userId: string, // 2026-08-25：必填（路由分区 + 列表过滤依据）
    studentName: string = '', // 可选：触发脱敏（仅 user_id != 'public' 时有效）
    signal?: AbortSignal,
  ): Promise<DocumentsUploadResult> {
    if (!userId) throw new Error('请先登录后再上传文档')
    if (files.length === 0) throw new Error('请先选择文件')
    if (files.length > 5) throw new Error('单次最多 5 份文件')
    const form = new FormData()
    files.forEach((f) => form.append('files', f))
    form.append('dataset_name', datasetName)
    form.append('chunk_strategy', chunkStrategy)
    form.append('user_id', userId)
    if (studentName) form.append('student_name', studentName)
    const res = await fetch(`${API_BASE}/documents/upload`, { method: 'POST', body: form, signal })
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      throw new Error(errorMessage(err, res.statusText))
    }
    return unwrapEnvelope(await res.json())
  },

  /** 2026-08-25：DocumentsPage 上传后展示已上传列表。 */
  async documentsList(
    userId: string,
    includePublic: boolean = true,
    signal?: AbortSignal,
  ): Promise<DocumentsListResult> {
    if (!userId) throw new Error('请先登录后再加载知识库列表')
    const params = new URLSearchParams({
      user_id: userId,
      include_public: String(includePublic),
    })
    const res = await fetch(`${API_BASE}/documents/datasets?${params}`, { signal })
    if (!res.ok) {
      const err = await res.json().catch(() => null)
      throw new Error(errorMessage(err, res.statusText))
    }
    return unwrapEnvelope(await res.json())
  },
}

// 兼容旧引用（RecommendPage/MonitorPage/StreamView 使用）
export type { RecommendationResponse }
