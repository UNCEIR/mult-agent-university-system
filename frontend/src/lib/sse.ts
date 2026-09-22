// 共享 SSE 消费器：fetch + ReadableStream 解析 `event:` / `data:` / `id:` 行
// → yield {event, data, id}。
//
// 路 2 升级：
// - 解析 SSE 标准 `id:` 字段（供 Last-Event-ID 续传使用）
// - 新增 consumeSSEWithRetry：指数退避重连，重连时带 Last-Event-ID header
// - 默认最多重试 3 次（指数 500ms / 1s / 2s），可通过 options 覆盖

export interface SSEParsedEvent {
  event: string
  data: unknown
  /** SSE `id:` 字段（后端按 thread_id 单调递增），用于断点续传。 */
  id?: string
}

export interface ConsumeSSEOptions {
  /** 最大重试次数（含首次连接）；默认 3。设 1 即不重试。 */
  maxAttempts?: number
  /** 退避基数（ms）；实际退避 = base * 2^(attempt-1)。默认 500。 */
  retryBaseMs?: number
  /** 透传给 fetch 的额外 header（除 Last-Event-ID 外）。 */
  extraHeaders?: Record<string, string>
  /** 每次重连前的回调（用于埋点 / UI 提示）。 */
  onRetry?: (attempt: number, delayMs: number, lastEventId: string | undefined) => void
  /** 仅消费缓存中 id > lastEventId 的事件后退出（用于前端手动重连场景）。 */
  replayOnly?: boolean
}

const DEFAULT_MAX_ATTEMPTS = 3
const DEFAULT_RETRY_BASE_MS = 500

export async function* consumeSSE(
  url: string,
  init: RequestInit,
  signal?: AbortSignal
): AsyncGenerator<SSEParsedEvent> {
  const res = await fetch(url, { ...init, signal })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  yield* parseSseStream(res.body!, signal)
}

/**
 * 消费 SSE 流 + 指数退避重连 + Last-Event-ID 续传。
 * - 第一次连接失败 / 流中途断开时自动重试（最多 maxAttempts 次）
 * - 每次重连都把 last received event id 写到 Last-Event-ID header
 * - 重连成功后从 last_event_id 之后的事件继续消费（服务端会先回放缓存）
 * - 用户主动 abort（signal.aborted）立即停止重试
 */
export async function* consumeSSEWithRetry(
  url: string,
  init: RequestInit,
  signal?: AbortSignal,
  options: ConsumeSSEOptions = {},
): AsyncGenerator<SSEParsedEvent> {
  const maxAttempts = options.maxAttempts ?? DEFAULT_MAX_ATTEMPTS
  const baseMs = options.retryBaseMs ?? DEFAULT_RETRY_BASE_MS
  let lastEventId: string | undefined
  let attempt = 0

  while (attempt < maxAttempts) {
    attempt++
    if (signal?.aborted) return

    const headers: Record<string, string> = {
      ...(init.headers as Record<string, string> | undefined),
      ...options.extraHeaders,
    }
    if (lastEventId !== undefined) {
      headers['Last-Event-ID'] = lastEventId
    }

    try {
      const res = await fetch(url, { ...init, headers, signal })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      // 仅在本次连接内去重。跨连接时服务端按 Last-Event-ID 回放，
      // 不能在客户端跨连接强去重，否则 Redis 降级导致 ID 重置时会误丢新事件。
      let connectionConsumedNumericId: number | undefined
      for await (const evt of parseSseStream(res.body!, signal)) {
        if (evt.id) {
          const numericId = Number(evt.id)
          if (Number.isFinite(numericId)) {
            if (
              connectionConsumedNumericId !== undefined &&
              numericId <= connectionConsumedNumericId
            ) {
              continue
            }
            connectionConsumedNumericId = numericId
          }
          lastEventId = evt.id
        }
        yield evt
      }
      // 流正常结束 → 直接退出（不重试）
      return
    } catch (err) {
      // 用户主动 abort：不再重试
      if (signal?.aborted) return
      // 还有重试余量：等退避时间后重试
      if (attempt < maxAttempts) {
        const delay = baseMs * Math.pow(2, attempt - 1)
        options.onRetry?.(attempt, delay, lastEventId)
        await sleep(delay, signal)
        continue
      }
      // 已达最大重试：抛出最后一次错误
      throw err
    }
  }
}

/** 解析 SSE 流（Readablestream → 事件），不处理重连；consumeSSEWithRetry 与 consumeSSE 共用。 */
async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<SSEParsedEvent> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  // 修复：eventType 必须在 while 循环外声明；之前在循环内每次 reader.read() 都被重置，
  // 当 SSE 事件跨 chunk 边界时（典型场景），event/data 会脱钩。
  let eventType = ''
  let lastId: string | undefined
  try {
    while (true) {
      if (signal?.aborted) return
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''
      for (const line of lines) {
        if (line.startsWith('id: ')) {
          lastId = line.slice(4).trim()
        } else if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim()
        } else if (line.startsWith('data: ')) {
          const raw = line.slice(6)
          try {
            const evt: SSEParsedEvent = {
              event: eventType,
              data: JSON.parse(raw),
            }
            if (lastId !== undefined) evt.id = lastId
            yield evt
          } catch {
            // skip unparseable lines
          }
          eventType = ''
        }
      }
    }
  } finally {
    try {
      reader.releaseLock()
    } catch {
      // ignore
    }
  }
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      signal?.removeEventListener('abort', onAbort)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}
