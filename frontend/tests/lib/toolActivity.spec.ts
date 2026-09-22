import { describe, expect, it } from 'vitest'

import { upsertToolActivity } from '@/lib/toolActivity'

describe('upsertToolActivity', () => {
  it('keeps same-name parallel calls separate by tool_call_id', () => {
    const startA = { tool: 'web_search', status: 'start' as const, session_id: 's', tool_call_id: 'a' }
    const startB = { tool: 'web_search', status: 'start' as const, session_id: 's', tool_call_id: 'b' }
    let tools = upsertToolActivity([], startA)
    tools = upsertToolActivity(tools, startB)
    tools = upsertToolActivity(tools, {
      tool: 'web_search',
      status: 'end',
      session_id: 's',
      tool_call_id: 'a',
      ok: false,
      code: 'TOOL_TIMEOUT',
      message: '查询超时',
    })

    expect(tools).toHaveLength(2)
    expect(tools[0]).toMatchObject({ tool_call_id: 'a', status: 'end', ok: false, code: 'TOOL_TIMEOUT' })
    expect(tools[1]).toMatchObject({ tool_call_id: 'b', status: 'start' })
  })

  it('does not retain legacy raw result payloads', () => {
    let tools = upsertToolActivity([], {
      tool: 'query_transcript',
      status: 'start',
      session_id: 's',
      tool_call_id: 'call-1',
    })
    tools = upsertToolActivity(tools, {
      tool: 'query_transcript',
      status: 'end',
      session_id: 's',
      tool_call_id: 'call-1',
      ok: true,
      result: '3 matches',
    })
    expect(tools[0]).toMatchObject({ status: 'end', ok: true })
    expect('result' in tools[0]).toBe(false)
  })
})
