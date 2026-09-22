// SSE event schema（zod）：运行时校验 + 静态类型推导。
// 后端 EventBuffer 给每条事件分配单调递增的 id（SSE 标准 `id:` 字段），
// 客户端解析 `id` 用于 Last-Event-ID 续传。
//
// 用途：
// - consumeSSE 解析后用 schema.safeParse 校验：失败则丢弃（不抛错影响流）
// - api.ts 类型注解：SSEEvent = ChatEvent | ReportEvent | EvaluationEvent | RecommendEvent
//
// 注意：zod `z.object` 默认是 **strip** 行为 —— 未声明的字段会被丢弃，不会"放行"。
// 新增后端字段（如 agent_tree / tool result）必须在此显式声明，否则解析后丢失（Phase 4 R4）。

import { z } from 'zod'

// ── 通用事件 schema ──
const idField = z.string().optional()
const sessionIdField = z.string()

// ── Chat SSE events（POST /api/v1/chat/stream） ──
export const ChatTextDataSchema = z.object({
  token: z.string(),
  session_id: sessionIdField,
})
export type ChatTextData = z.infer<typeof ChatTextDataSchema>

export const ChatToolDataSchema = z.object({
  tool: z.string(),
  status: z.enum(['start', 'end']),
  session_id: sessionIdField,
  tool_call_id: z.string().optional(),
  run_id: z.string().optional(),
  args: z.record(z.unknown()).optional(),
  // tool end：ok=false 表示工具级失败；顶层 error 仅用于 Agent/框架级失败。
  ok: z.boolean().optional(),
  code: z.string().optional(),
  message: z.string().optional(),
  retryable: z.boolean().optional(),
  latency_ms: z.number().nullable().optional(),
  /** @deprecated legacy 原始工具结果，chat 前端不再保存或渲染。 */
  result: z.string().optional(),
})
export type ChatToolData = z.infer<typeof ChatToolDataSchema>

// ── Phase 4 E3：agent_tree（委派树）契约 ──
export const AgentTreeNodeSchema: z.ZodType<AgentTreeNode> = z.lazy(() =>
  z.object({
    run_id: z.string(),
    name: z.string(),
    kind: z.enum(['main', 'subagent']),
    status: z.string(),
    args_summary: z.string().nullable().optional(),
    result_summary: z.string().nullable().optional(),
    latency_ms: z.number().nullable().optional(),
    children: z.array(AgentTreeNodeSchema),
  }),
)
export type AgentTreeNode = {
  run_id: string
  name: string
  kind: 'main' | 'subagent'
  status: string
  args_summary?: string | null
  result_summary?: string | null
  latency_ms?: number | null
  children: AgentTreeNode[]
}

export const ChatDoneDataSchema = z.object({
  reply: z.string(),
  messages_count: z.number().int().nonnegative(),
  session_id: sessionIdField,
  usage: z.record(z.unknown()).optional(),
  latency_ms: z.number().nullable().optional(),
  ttft_ms: z.number().nullable().optional(),
  agent_tree: z.array(AgentTreeNodeSchema).optional(),
  last_event_id: z.number().nullable().optional(),
})
export type ChatDoneData = z.infer<typeof ChatDoneDataSchema>

export const ChatErrorDataSchema = z.object({
  code: z.string(),
  message: z.string(),
  session_id: sessionIdField,
})
export type ChatErrorData = z.infer<typeof ChatErrorDataSchema>

// ── Recommend SSE events（POST /api/v1/recommend/stream） ──
export const SSEPhaseDataSchema = z.object({
  phase: z.string(),
  request_id: z.string().optional(),
  num_items: z.number().optional(),
  profile_extracted: z.boolean().optional(),
  warning_count: z.number().optional(),
})
export type SSEPhaseData = z.infer<typeof SSEPhaseDataSchema>

export const SSECourseStartDataSchema = z.object({
  course_id: z.string(),
  course_name: z.string(),
  index: z.number().int().nonnegative().optional(),
})
export type SSECourseStartData = z.infer<typeof SSECourseStartDataSchema>

export const SSETextDataSchema = z.object({
  course_id: z.string().nullable(),
  token: z.string(),
})
export type SSETextData = z.infer<typeof SSETextDataSchema>

export const SSEDoneDataSchema = z.object({
  request_id: z.string(),
  user_id: z.string(),
  courses: z.array(z.record(z.unknown())),
  selection_warnings: z.array(z.record(z.unknown())),
  experiment_group: z.string(),
  agent_results: z.record(z.unknown()),
  total_latency_ms: z.number(),
  last_event_id: z.number().nullable().optional(),
})
export type SSEDoneData = z.infer<typeof SSEDoneDataSchema>

export const SSEErrorDataSchema = z.object({
  code: z.string(),
  message: z.string(),
  phase: z.string().optional(),
})
export type SSEErrorData = z.infer<typeof SSEErrorDataSchema>

// ── Report SSE events（POST /api/v1/report） ──
export const ReportStudentDoneDataSchema = z.object({
  student_id: z.string(),
  name: z.string(),
  status: z.string(),
  format: z.string(),
  url: z.string(),
})
export type ReportStudentDoneData = z.infer<typeof ReportStudentDoneDataSchema>

export const ReportStudentErrorDataSchema = z.object({
  student_id: z.string().optional(),
  name: z.string().optional(),
  reason: z.string().optional(),
  code: z.string().optional(),
})
export type ReportStudentErrorData = z.infer<typeof ReportStudentErrorDataSchema>

export const ReportDoneDataSchema = z.object({
  batch_id: z.string(),
  students: z.array(ReportStudentDoneDataSchema),
  failed_students: z.array(ReportStudentErrorDataSchema),
  warnings: z.array(z.string()).optional(),
  summary: z.string().optional(),
})
export type ReportDoneData = z.infer<typeof ReportDoneDataSchema>

// ── Evaluation SSE events（POST /api/v1/evaluation） ──
export const EvaluationStageDataSchema = z.object({
  stage: z.string(),
  detail: z.string().optional(),
})
export type EvaluationStageData = z.infer<typeof EvaluationStageDataSchema>

export const EvaluationRadarDataSchema = z.object({
  target_user_id: z.string(),
  dimensions: z.array(
    z.object({
      name: z.string(),
      metric: z.string(),
      value: z.number(),
      weight: z.number().optional(),
    }),
  ),
  rejected: z.array(z.string()).optional(),
  overall_theme: z.string().optional(),
  status: z.string().optional(),
})
export type EvaluationRadarData = z.infer<typeof EvaluationRadarDataSchema>

export const EvaluationCommentTokenDataSchema = z.object({
  token: z.string(),
})
export type EvaluationCommentTokenData = z.infer<typeof EvaluationCommentTokenDataSchema>

export const EvaluationDoneDataSchema = z.object({
  evaluation_id: z.string(),
  target_user_id: z.string(),
  comment_type: z.string(),
  radar: EvaluationRadarDataSchema,
  comment: z.string(),
  status: z.string(),
  comment_status: z.string(),
  usage: z.record(z.unknown()).optional(),
})
export type EvaluationDoneData = z.infer<typeof EvaluationDoneDataSchema>

// ── 顶层事件 schema（按 event 字段分派） ──
export const ChatEventSchema = z.discriminatedUnion('event', [
  z.object({ event: z.literal('text'), data: ChatTextDataSchema, id: idField }),
  z.object({ event: z.literal('tool'), data: ChatToolDataSchema, id: idField }),
  z.object({ event: z.literal('done'), data: ChatDoneDataSchema, id: idField }),
  z.object({ event: z.literal('error'), data: ChatErrorDataSchema, id: idField }),
])
export type ChatEvent = z.infer<typeof ChatEventSchema>

export const ReportEventSchema = z.discriminatedUnion('event', [
  z.object({ event: z.literal('text'), data: z.object({ token: z.string() }), id: idField }),
  z.object({ event: z.literal('tool'), data: ChatToolDataSchema, id: idField }),
  z.object({ event: z.literal('progress'), data: z.record(z.unknown()), id: idField }),
  z.object({ event: z.literal('student_done'), data: ReportStudentDoneDataSchema, id: idField }),
  z.object({ event: z.literal('student_error'), data: ReportStudentErrorDataSchema, id: idField }),
  z.object({ event: z.literal('done'), data: ReportDoneDataSchema, id: idField }),
  z.object({ event: z.literal('error'), data: ChatErrorDataSchema, id: idField }),
])
export type ReportEvent = z.infer<typeof ReportEventSchema>

export const EvaluationEventSchema = z.discriminatedUnion('event', [
  z.object({ event: z.literal('stage'), data: EvaluationStageDataSchema, id: idField }),
  z.object({ event: z.literal('radar'), data: EvaluationRadarDataSchema, id: idField }),
  z.object({ event: z.literal('comment_token'), data: EvaluationCommentTokenDataSchema, id: idField }),
  z.object({ event: z.literal('done'), data: EvaluationDoneDataSchema, id: idField }),
  z.object({ event: z.literal('error'), data: ChatErrorDataSchema, id: idField }),
])
export type EvaluationEvent = z.infer<typeof EvaluationEventSchema>

export const RecommendEventSchema = z.discriminatedUnion('event', [
  z.object({ event: z.literal('phase'), data: SSEPhaseDataSchema, id: idField }),
  z.object({ event: z.literal('course_start'), data: SSECourseStartDataSchema, id: idField }),
  z.object({ event: z.literal('course_end'), data: z.object({ course_id: z.string() }), id: idField }),
  z.object({ event: z.literal('text'), data: SSETextDataSchema, id: idField }),
  z.object({ event: z.literal('done'), data: SSEDoneDataSchema, id: idField }),
  z.object({ event: z.literal('error'), data: SSEErrorDataSchema, id: idField }),
])
export type RecommendEvent = z.infer<typeof RecommendEventSchema>

/**
 * 安全解析 SSE 事件：schema 不匹配时返回 null（不抛错影响流）。
 * 路 2 SSE 协议扩展（id: 字段）兼容：新字段必须显式声明（zod 默认 strip 丢弃未知键，
 * 若确需透传未知键才用 .passthrough()）。
 */
export function safeParseEvent<S extends z.ZodTypeAny>(
  schema: S,
  eventName: string,
  data: unknown,
): z.infer<S> | null {
  const result = schema.safeParse(data)
  if (!result.success) {
    if (typeof console !== 'undefined') {
      console.warn(`[sse schema] ${eventName} payload mismatch:`, result.error.format())
    }
    return null
  }
  return result.data
}

export const SSEEventIdSchema = z
  .string()
  .transform((s) => s.trim())
  .pipe(z.string().regex(/^\d+$/, 'event_id must be a numeric string'))
  .transform((s) => Number(s))
