import type { ChatToolData } from '../types/sse'
import type { ToolActivity } from '../components/AgentActivityTimeline'

function activityKey(activity: ToolActivity): string {
  return activity.tool_call_id || activity.run_id || activity.name
}

function eventKey(data: ChatToolData): string {
  return data.tool_call_id || data.run_id || data.tool
}

/** 以 tool_call_id 为主键合并工具事件；旧事件缺少 ID 时回退 run_id/name。 */
export function upsertToolActivity(
  tools: ToolActivity[],
  data: ChatToolData,
): ToolActivity[] {
  const key = eventKey(data)
  const next = tools.map((tool) => ({ ...tool }))
  const index = next.findIndex((tool) => activityKey(tool) === key)
  const activity: ToolActivity = {
    name: data.tool,
    status: data.status,
    tool_call_id: data.tool_call_id,
    run_id: data.run_id,
    ok: data.ok,
    code: data.code,
    message: data.message,
    retryable: data.retryable,
    latency_ms: data.latency_ms,
  }

  if (index < 0) {
    next.push(activity)
    return next
  }

  const previous = next[index]
  next[index] = {
    ...previous,
    ...activity,
    tool_call_id: data.tool_call_id || previous.tool_call_id,
    run_id: data.run_id || previous.run_id,
    ok: data.ok ?? previous.ok,
    code: data.code || previous.code,
    message: data.message || previous.message,
    retryable: data.retryable ?? previous.retryable,
    latency_ms: data.latency_ms ?? previous.latency_ms,
  }
  return next
}
