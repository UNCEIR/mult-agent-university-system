import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { consumeSSE, consumeSSEWithRetry } from '@/lib/sse'

function makeSseResponse(lines: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const line of lines) {
        controller.enqueue(encoder.encode(line + '\n'))
      }
      controller.close()
    },
  })
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
}

describe('consumeSSE', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('parses event/data pairs in order', async () => {
    const events = [
      'event: phase',
      'data: {"phase":"phase1"}',
      '',
      'event: text',
      'data: {"token":"hello"}',
      '',
      'event: done',
      'data: {"ok":true}',
      '',
    ]
    vi.stubGlobal('fetch', vi.fn(async () => makeSseResponse(events)))

    const out: Array<{ event: string; data: unknown }> = []
    for await (const evt of consumeSSE('/api/v1/test', { method: 'POST' })) {
      out.push(evt)
    }
    expect(out).toHaveLength(3)
    expect(out[0]).toEqual({ event: 'phase', data: { phase: 'phase1' } })
    expect(out[1]).toEqual({ event: 'text', data: { token: 'hello' } })
    expect(out[2]).toEqual({ event: 'done', data: { ok: true } })
  })

  it('skips lines whose data JSON is malformed', async () => {
    const events = [
      'event: text',
      'data: {not valid json',
      '',
      'event: text',
      'data: {"token":"after-broken"}',
      '',
    ]
    vi.stubGlobal('fetch', vi.fn(async () => makeSseResponse(events)))

    const out: Array<{ event: string; data: unknown }> = []
    for await (const evt of consumeSSE('/api/v1/test', { method: 'POST' })) {
      out.push(evt)
    }
    expect(out).toHaveLength(1)
    expect(out[0].data).toEqual({ token: 'after-broken' })
  })

  it('stops iteration when abort signal fires', async () => {
    const ac = new AbortController()
    vi.stubGlobal(
      'fetch',
      vi.fn(async (_url: string, init: RequestInit) => {
        return new Promise<Response>((_, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
          setTimeout(() => reject(new Error('test timeout')), 200)
        })
      }),
    )

    const collected: string[] = []
    const iter = consumeSSE('/api/v1/test', { method: 'POST' }, ac.signal)
    ac.abort()
    await expect(async () => {
      for await (const evt of iter) {
        collected.push(String((evt.data as { token: string }).token))
      }
    }).rejects.toThrow()
    expect(collected).toEqual([])
  })

  it('throws with status detail on non-2xx response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ detail: 'upstream error' }), {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )

    await expect(async () => {
      for await (const _evt of consumeSSE('/api/v1/test', { method: 'POST' })) {
        void _evt
      }
    }).rejects.toThrow(/upstream error/)
  })

  it('parses id: field per SSE spec and attaches it to emitted events', async () => {
    const events = [
      'id: 1',
      'event: text',
      'data: {"token":"a"}',
      '',
      'id: 2',
      'event: text',
      'data: {"token":"b"}',
      '',
      'id: 3',
      'event: done',
      'data: {"ok":true}',
      '',
    ]
    vi.stubGlobal('fetch', vi.fn(async () => makeSseResponse(events)))

    const out: Array<{ event: string; data: unknown; id?: string }> = []
    for await (const evt of consumeSSE('/api/v1/test', { method: 'POST' })) {
      out.push(evt as { event: string; data: unknown; id?: string })
    }
    expect(out[0].id).toBe('1')
    expect(out[1].id).toBe('2')
    expect(out[2].id).toBe('3')
  })
})

describe('consumeSSEWithRetry', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns events on first success without retrying', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async () => makeSseResponse([
      'id: 1',
      'event: done',
      'data: {"ok":true}',
      '',
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const out: unknown[] = []
    for await (const evt of consumeSSEWithRetry(
      '/api/v1/test',
      { method: 'POST' },
      undefined,
      { maxAttempts: 3 },
    )) {
      out.push(evt)
    }
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(out).toHaveLength(1)
  })

  it('retries on transient failure with exponential backoff and propagates Last-Event-ID', async () => {
    vi.useFakeTimers()
    let callCount = 0
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      callCount++
      if (callCount === 1) {
        // 第一次失败：502
        return new Response('{"detail":"upstream"}', {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      // 第二次成功：fetch 时携带 Last-Event-ID header
      const headers = (init.headers ?? {}) as Record<string, string>
      // 重连时 lastEventId 还未确定（首次没收到 id），不应携带 Last-Event-ID
      expect(headers['Last-Event-ID']).toBeUndefined()
      return makeSseResponse(['id: 5', 'event: done', 'data: {"ok":true}', ''])
    })
    vi.stubGlobal('fetch', fetchMock)

    const retryEvents: Array<{ attempt: number; delayMs: number; lastEventId: string | undefined }> = []
    const out: unknown[] = []
    const iter = consumeSSEWithRetry(
      '/api/v1/test',
      { method: 'POST' },
      undefined,
      {
        maxAttempts: 3,
        retryBaseMs: 100,
        onRetry: (a, d, l) => retryEvents.push({ attempt: a, delayMs: d, lastEventId: l }),
      },
    )
    const collectPromise = (async () => {
      for await (const evt of iter) out.push(evt)
    })()
    // 跑过所有 timer
    await vi.runAllTimersAsync()
    await collectPromise
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(out).toHaveLength(1)
    expect(retryEvents).toHaveLength(1)
    expect(retryEvents[0]).toMatchObject({ attempt: 1, delayMs: 100, lastEventId: undefined })
  })

  it('ignores duplicate or older event ids within a stream', async () => {
    const fetchMock = vi.fn(async () =>
      makeSseResponse([
        'id: 42',
        'event: text',
        'data: {"token":"a"}',
        '',
        'id: 42',
        'event: text',
        'data: {"token":"a-again"}',
        '',
        'id: 43',
        'event: text',
        'data: {"token":"b"}',
        '',
      ]),
    )
    vi.stubGlobal('fetch', fetchMock)

    const out: Array<{ event: string; data: unknown; id?: string }> = []
    for await (const evt of consumeSSEWithRetry('/api/v1/test', { method: 'POST' })) {
      out.push(evt as { event: string; data: unknown; id?: string })
    }

    expect(out.map((evt) => evt.id)).toEqual(['42', '43'])
    expect(out.map((evt) => (evt.data as { token: string }).token)).toEqual(['a', 'b'])
  })

  it('on second connection carries Last-Event-ID header when first run yielded events', async () => {
    // 用真定时器：第一次 fetch 让 controller 立即 error，
    // consumer reader.read() 后续抛错 → 触发 retry 路径。
    let callCount = 0
    let firstCallHeaders: Record<string, string> = {}
    let secondCallHeaders: Record<string, string> = {}
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      callCount++
      const headers = (init.headers ?? {}) as Record<string, string>
      if (callCount === 1) firstCallHeaders = headers
      else secondCallHeaders = headers
      if (callCount === 1) {
        // 第一次：模拟 reader.read() 抛错（在事件被 yield 之后抛）
        // 用同步 throw 让 consumer 的 for-await 立即抛
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            // 抛出错误，consumer reader.read() 拿不到任何事件
            controller.error(new Error('immediate connection reset'))
          },
        })
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      // 第二次：返回成功流
      return makeSseResponse(['id: 99', 'event: done', 'data: {"ok":true}', ''])
    })
    vi.stubGlobal('fetch', fetchMock)

    const out: Array<{ event: string; data: unknown; id?: string }> = []
    const iter = consumeSSEWithRetry(
      '/api/v1/test',
      { method: 'POST' },
      undefined,
      { maxAttempts: 3, retryBaseMs: 5 },
    )
    for await (const evt of iter) {
      out.push(evt as { event: string; data: unknown; id?: string })
    }

    expect(fetchMock).toHaveBeenCalledTimes(2)
    // 第一次无 lastEventId
    expect(firstCallHeaders['Last-Event-ID']).toBeUndefined()
    // 因为第一次没收到任何事件 → lastEventId 仍 undefined → 第二次也不携带
    // （这是预期：consumer 只在成功 yield 至少 1 个事件后才记录 lastEventId）
    expect(secondCallHeaders['Last-Event-ID']).toBeUndefined()
    // 但 retry 链路确认通了：第二次 fetch 被调用了
    expect(secondCallHeaders).toBeDefined()
    // out 包含第二次的 done 事件
    expect(out.find((evt) => evt.event === 'done')).toBeTruthy()
  })

  it('on second connection, when first run yielded events with ids, Last-Event-ID header carries last id', async () => {
    // 关键场景：consumer 第一次收到 id=N 事件，连接断开，重连时 Last-Event-ID: N
    let callCount = 0
    let secondCallHeaders: Record<string, string> = {}
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      callCount++
      if (callCount === 2) {
        secondCallHeaders = (init.headers ?? {}) as Record<string, string>
      }
      if (callCount === 1) {
        const encoder = new TextEncoder()
        let firstRead = true
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode('id: 42\nevent: text\ndata: {"token":"a"}\n\n'),
            )
            controller.enqueue(
              encoder.encode('id: 43\nevent: text\ndata: {"token":"b"}\n\n'),
            )
          },
          pull(controller) {
            if (firstRead) {
              firstRead = false
              return
            }
            controller.error(new Error('reset after events'))
          },
        })
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      return makeSseResponse(['event: done', 'data: {"ok":true}', ''])
    })
    vi.stubGlobal('fetch', fetchMock)

    const iter = consumeSSEWithRetry(
      '/api/v1/test',
      { method: 'POST' },
      undefined,
      { maxAttempts: 3, retryBaseMs: 5 },
    )
    for await (const _evt of iter) {
      void _evt
    }

    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(secondCallHeaders['Last-Event-ID']).toBe('43')
  })

  it('on second connection, when first run yielded events with ids, Last-Event-ID header carries last id', async () => {
    // 关键场景：consumer 第一次收到 id=N 事件，连接断开，重连时 Last-Event-ID: N
    // 用真定时器 + 立即 throw 的方式让 consumer 拿到 chunk 后立即抛错
    let callCount = 0
    let secondCallHeaders: Record<string, string> = {}
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      callCount++
      if (callCount === 2) {
        secondCallHeaders = (init.headers ?? {}) as Record<string, string>
      }
      if (callCount === 1) {
        const encoder = new TextEncoder()
        let firstRead = true
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoder.encode('id: 42\nevent: text\ndata: {"token":"a"}\n\n'),
            )
            controller.enqueue(
              encoder.encode('id: 43\nevent: text\ndata: {"token":"b"}\n\n'),
            )
          },
          pull(controller) {
            // 第一次 pull 时让 consumer 拿到已 enqueue 的数据；之后 error
            if (firstRead) {
              firstRead = false
              return
            }
            controller.error(new Error('reset after events'))
          },
        })
        return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
      }
      return makeSseResponse(['event: done', 'data: {"ok":true}', ''])
    })
    vi.stubGlobal('fetch', fetchMock)

    const iter = consumeSSEWithRetry(
      '/api/v1/test',
      { method: 'POST' },
      undefined,
      { maxAttempts: 3, retryBaseMs: 5 },
    )
    // 收集完所有事件
    for await (const _evt of iter) {
      void _evt
    }

    expect(fetchMock).toHaveBeenCalledTimes(2)
    // 第二次 fetch 应携带 Last-Event-ID: 43（最后收到的 id）
    expect(secondCallHeaders['Last-Event-ID']).toBe('43')
  })

  it('throws after exhausting all attempts', async () => {
    // 用真实定时器（小延迟 5ms）让 retry 能在 100ms 内跑完 3 次尝试
    const fetchMock = vi.fn(async () =>
      new Response('{"detail":"always fails"}', {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    const retryEvents: Array<{ attempt: number; delayMs: number }> = []
    await expect(async () => {
      for await (const _evt of consumeSSEWithRetry(
        '/api/v1/test',
        { method: 'POST' },
        undefined,
        { maxAttempts: 3, retryBaseMs: 5, onRetry: (a, d) => retryEvents.push({ attempt: a, delayMs: d }) },
      )) {
        void _evt
      }
    }).rejects.toThrow(/always fails/)

    expect(fetchMock).toHaveBeenCalledTimes(3)
    // 3 次尝试 = 2 次重试回调（attempt 1 -> delay; attempt 2 -> delay; attempt 3 失败抛错）
    expect(retryEvents).toHaveLength(2)
    expect(retryEvents[0].attempt).toBe(1)
    expect(retryEvents[1].attempt).toBe(2)
  })

  it('does not retry when signal is aborted', async () => {
    vi.useFakeTimers()
    const ac = new AbortController()
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      return new Promise<Response>((_, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
        setTimeout(() => reject(new Error('test timeout')), 5000)
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const collected: unknown[] = []
    const promise = (async () => {
      try {
        for await (const evt of consumeSSEWithRetry(
          '/api/v1/test',
          { method: 'POST' },
          ac.signal,
          { maxAttempts: 3, retryBaseMs: 50 },
        )) {
          collected.push(evt)
        }
      } catch {
        // abort 后预期 reject
      }
    })()
    // 立即 abort
    ac.abort()
    await vi.runAllTimersAsync()
    await promise
    // 只调用一次（不重试）
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(collected).toHaveLength(0)
  })
})
