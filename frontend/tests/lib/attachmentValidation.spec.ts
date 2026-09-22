import { describe, expect, it } from 'vitest'

import {
  createAttachmentValidationStrategy,
  getChatAttachmentValidationStrategy,
} from '@/lib/attachments'

function file(name: string, type: string, size = 4): File {
  const f = new File([new Uint8Array(size)], name, { type })
  Object.defineProperty(f, 'size', { value: size })
  return f
}

describe('attachment validation strategies', () => {
  it('composes custom rules with the shared validation engine', () => {
    const strategy = createAttachmentValidationStrategy({
      kind: 'text',
      accept: '.txt',
      maxFiles: 2,
      maxFileBytes: 10,
      rules: [
        (item) =>
          item.type === 'text/plain'
            ? null
            : {
                code: 'UNSUPPORTED_TYPE',
                message: `${item.name}：仅支持 TXT`,
                fileName: item.name,
              },
      ],
    })

    const result = strategy.validate([
      file('ok.txt', 'text/plain', 4),
      file('bad.pdf', 'application/pdf', 4),
    ])

    expect(result.accepted.map((item) => item.name)).toEqual(['ok.txt'])
    expect(result.rejected.map((item) => item.name)).toEqual(['bad.pdf'])
    expect(result.issues).toMatchObject([
      { code: 'UNSUPPORTED_TYPE', fileName: 'bad.pdf' },
    ])
  })

  it('keeps partial acceptance while enforcing count and size limits', () => {
    const strategy = createAttachmentValidationStrategy({
      kind: 'image',
      accept: 'image/*',
      maxFiles: 2,
      maxFileBytes: 10,
      maxTotalBytes: 14,
      rules: [],
    })

    const result = strategy.validate(
      [
        file('a.png', 'image/png', 6),
        file('b.png', 'image/png', 6),
        file('c.png', 'image/png', 8),
      ],
      { currentCount: 1, currentBytes: 6 },
    )

    expect(result.accepted.map((item) => item.name)).toEqual(['a.png'])
    expect(result.rejected.map((item) => item.name)).toEqual(['b.png', 'c.png'])
    expect(result.issues.map((issue) => issue.code)).toEqual([
      'TOO_MANY_FILES',
      'TOO_MANY_FILES',
    ])
  })

  it('resolves the image strategy from the chat attachment registry', () => {
    const strategy = getChatAttachmentValidationStrategy('image')
    expect(strategy.kind).toBe('image')
    expect(strategy.maxFiles).toBe(4)
    expect(strategy.accept).toContain('image/png')
  })

  it('rejects unknown attachment kinds explicitly', () => {
    expect(() => getChatAttachmentValidationStrategy('audio')).toThrow(
      'Unknown chat attachment kind: audio',
    )
  })
})
