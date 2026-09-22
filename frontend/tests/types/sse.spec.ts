import { describe, expect, it } from 'vitest'

import {
  ChatEventSchema,
  RecommendEventSchema,
  ReportEventSchema,
  EvaluationEventSchema,
  ChatDoneDataSchema,
  SSEPhaseDataSchema,
  ReportStudentDoneDataSchema,
  safeParseEvent,
  SSEEventIdSchema,
} from '@/types/sse'

describe('zod SSE event schemas', () => {
  it('parses valid ChatEvent.text', () => {
    const result = ChatEventSchema.safeParse({
      event: 'text',
      data: { token: 'hi', session_id: 's1' },
    })
    expect(result.success).toBe(true)
  })

  it('parses valid ChatEvent.tool with args', () => {
    const result = ChatEventSchema.safeParse({
      event: 'tool',
      data: { tool: 'query_knowledge', status: 'start', session_id: 's1', args: { query: 'x' } },
    })
    expect(result.success).toBe(true)
  })

  it('parses ChatEvent.tool failure fields without stripping', () => {
    const result = ChatEventSchema.safeParse({
      event: 'tool',
      data: {
        tool: 'web_search',
        status: 'end',
        session_id: 's1',
        tool_call_id: 'call-1',
        ok: false,
        code: 'TOOL_TIMEOUT',
        message: '查询超时',
        retryable: true,
        latency_ms: 10020,
      },
    })
    expect(result.success).toBe(true)
    if (result.success && result.data.event === 'tool') {
      expect(result.data.data.tool_call_id).toBe('call-1')
      expect(result.data.data.ok).toBe(false)
      expect(result.data.data.code).toBe('TOOL_TIMEOUT')
      expect(result.data.data.retryable).toBe(true)
    }
  })

  it('rejects ChatEvent.text missing token', () => {
    const result = ChatEventSchema.safeParse({
      event: 'text',
      data: { session_id: 's1' },
    })
    expect(result.success).toBe(false)
  })

  it('ChatDoneDataSchema accepts last_event_id', () => {
    const result = ChatDoneDataSchema.safeParse({
      reply: 'ok',
      messages_count: 1,
      session_id: 's1',
      last_event_id: 42,
    })
    expect(result.success).toBe(true)
  })

  it('RecommendEventSchema parses phase + text + done', () => {
    const phase = RecommendEventSchema.safeParse({
      event: 'phase',
      data: { phase: 'phase1', warning_count: 0 },
    })
    expect(phase.success).toBe(true)
    const text = RecommendEventSchema.safeParse({
      event: 'text',
      data: { course_id: 'c1', token: '推荐' },
    })
    expect(text.success).toBe(true)
    const done = RecommendEventSchema.safeParse({
      event: 'done',
      data: {
        request_id: 'r1',
        user_id: 'u1',
        courses: [{ course_id: 'c1' }],
        selection_warnings: [],
        experiment_group: 'pipeline',
        agent_results: {},
        total_latency_ms: 1200,
      },
    })
    expect(done.success).toBe(true)
  })

  it('ReportEventSchema parses student_done/done with student arrays', () => {
    const studentDone = ReportEventSchema.safeParse({
      event: 'student_done',
      data: {
        student_id: 's1',
        name: '张三',
        status: 'ok',
        format: 'pdf',
        url: '/dl?token=xxx',
      },
    })
    expect(studentDone.success).toBe(true)
    const done = ReportEventSchema.safeParse({
      event: 'done',
      data: {
        batch_id: 'b1',
        students: [],
        failed_students: [],
      },
    })
    expect(done.success).toBe(true)
  })

  it('EvaluationEventSchema parses radar + comment_token + done', () => {
    const radar = EvaluationEventSchema.safeParse({
      event: 'radar',
      data: {
        target_user_id: 'u1',
        dimensions: [{ name: '学术', metric: 'gpa', value: 3.5 }],
      },
    })
    expect(radar.success).toBe(true)
    const commentToken = EvaluationEventSchema.safeParse({
      event: 'comment_token',
      data: { token: '学生表现优秀' },
    })
    expect(commentToken.success).toBe(true)
    const done = EvaluationEventSchema.safeParse({
      event: 'done',
      data: {
        evaluation_id: 'e1',
        target_user_id: 'u1',
        comment_type: 'semester_summary',
        radar: { target_user_id: 'u1', dimensions: [] },
        comment: '评语正文',
        status: 'ok',
        comment_status: 'llm',
      },
    })
    expect(done.success).toBe(true)
  })

  it('safeParseEvent returns null on schema mismatch (logs warning, never throws)', () => {
    // 故意构造缺字段的 payload
    const parsed = safeParseEvent(
      SSEPhaseDataSchema,
      'phase',
      { /* 缺 phase */ warning_count: 1 },
    )
    expect(parsed).toBeNull()
  })

  it('safeParseEvent returns typed object on success', () => {
    const parsed = safeParseEvent(
      ReportStudentDoneDataSchema,
      'student_done',
      {
        student_id: 's1',
        name: '张三',
        status: 'ok',
        format: 'pdf',
        url: '/dl?token=xxx',
      },
    )
    expect(parsed).toEqual({
      student_id: 's1',
      name: '张三',
      status: 'ok',
      format: 'pdf',
      url: '/dl?token=xxx',
    })
  })

  it('SSEEventIdSchema parses numeric string to number', () => {
    expect(SSEEventIdSchema.parse('42')).toBe(42)
    expect(SSEEventIdSchema.parse('  123  ')).toBe(123)
    expect(SSEEventIdSchema.safeParse('abc').success).toBe(false)
    expect(SSEEventIdSchema.safeParse('12.5').success).toBe(false)
  })

  it('all 4 event schemas accept optional id field', () => {
    // 每个 schema 都应允许顶层有 id（path 无关）
    // 通过 RecommendEvent + id 字段验证（其他 3 个 schema idField 同样定义）
    const result = RecommendEventSchema.safeParse({
      event: 'text',
      data: { course_id: null, token: 'x' },
      id: '99',
    })
    expect(result.success).toBe(true)
    if (result.success) {
      expect((result.data as { id?: string }).id).toBe('99')
    }
  })
})
