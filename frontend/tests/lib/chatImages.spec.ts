import { describe, expect, it } from 'vitest'
import {
  MAX_CHAT_IMAGES,
  parseHistoryAttachments,
  validateImageSelection,
} from '@/lib/chatImages'

function file(name: string, type: string, size = 4): File {
  const f = new File([new Uint8Array(size)], name, { type })
  Object.defineProperty(f, 'size', { value: size })
  return f
}

describe('chatImages', () => {
  it('accepts mainstream browser image formats', () => {
    const files = [
      file('a.png', 'image/png'),
      file('b.jpg', 'image/jpeg'),
      file('c.webp', 'image/webp'),
      file('d.gif', 'image/gif'),
      file('e.bmp', 'image/bmp'),
    ]
    for (const item of files) {
      const result = validateImageSelection([item])
      expect(result.accepted.map((accepted) => accepted.name)).toEqual([item.name])
      expect(result.errors).toEqual([])
    }
  })

  it('rejects unsupported format and invalid size', () => {
    const result = validateImageSelection([
      file('a.svg', 'image/svg+xml'),
      file('empty.png', 'image/png', 0),
      file('huge.png', 'image/png', 10 * 1024 * 1024 + 1),
    ])
    expect(result.accepted).toEqual([])
    expect(result.errors.join('；')).toContain('仅支持 PNG/JPEG/WebP/GIF/BMP')
    expect(result.errors.join('；')).toContain('文件内容为空')
    expect(result.errors.join('；')).toContain('不能超过 10MB')
  })

  it('enforces max image count', () => {
    const result = validateImageSelection([file('a.png', 'image/png')], MAX_CHAT_IMAGES)
    expect(result.accepted).toEqual([])
    expect(result.errors[0]).toContain(`最多上传 ${MAX_CHAT_IMAGES} 张`)
  })

  it('parses history attachments from JSON string', () => {
    const parsed = parseHistoryAttachments(
      JSON.stringify([{ image_id: 'img_1', preview_url: '/api/v1/chat/images/img_1/content?user_id=u' }]),
    )
    expect(parsed).toHaveLength(1)
    expect(parsed[0].image_id).toBe('img_1')
  })
})