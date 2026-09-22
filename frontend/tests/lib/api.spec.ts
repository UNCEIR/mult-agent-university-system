import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, unwrapEnvelope } from '@/lib/api'

describe('unwrapEnvelope', () => {
  it('unwraps BaseResult envelope and returns data', () => {
    const body = { code: 200, success: true, data: { count: 1, datasets: [] }, msg: '操作成功' }
    expect(unwrapEnvelope<{ count: number }>(body)).toEqual({ count: 1, datasets: [] })
  })

  it('returns non-envelope body as-is', () => {
    const raw = { status: 'ok', model: 'qwen' }
    expect(unwrapEnvelope(raw)).toEqual(raw)
  })

  it('returns null for failed envelope with null data', () => {
    const body = { code: 403, success: false, data: null, msg: '无权查看' }
    expect(unwrapEnvelope<null>(body)).toBeNull()
  })

  it('passes through primitive payloads', () => {
    expect(unwrapEnvelope(42)).toBe(42)
    expect(unwrapEnvelope(null)).toBeNull()
  })
})

describe('chat image upload API', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('posts multipart files with session and user ids', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          code: 200,
          success: true,
          data: { images: [{ image_id: 'img_1', preview_url: '/api/v1/chat/images/img_1/content?user_id=u1' }] },
          msg: '操作成功',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )
    vi.stubGlobal('fetch', fetchMock)

    const file = new File([new Uint8Array([1, 2, 3])], 'a.png', { type: 'image/png' })
    const result = await api.uploadChatImages([file], 's1', 'u1')

    expect(result.images[0].image_id).toBe('img_1')
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/chat/images/upload')
    expect(init.method).toBe('POST')
    const form = init.body as FormData
    expect(form.get('session_id')).toBe('s1')
    expect(form.get('user_id')).toBe('u1')
    expect(form.getAll('files')).toHaveLength(1)
  })
})