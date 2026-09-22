import { createAttachmentValidationStrategy } from './validation'

export const MAX_CHAT_IMAGES = 4
export const MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024
export const MAX_CHAT_TOTAL_BYTES = 30 * 1024 * 1024

export const SUPPORTED_IMAGE_MIME_TYPES = [
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/pjpeg',
  'image/webp',
  'image/gif',
  'image/bmp',
  'image/x-ms-bmp',
] as const

export const SUPPORTED_IMAGE_ACCEPT = [
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.gif',
  '.bmp',
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
  'image/bmp',
].join(',')

const SUPPORTED_IMAGE_MIME_SET = new Set<string>(SUPPORTED_IMAGE_MIME_TYPES)

export const imageAttachmentValidationStrategy = createAttachmentValidationStrategy({
  kind: 'image',
  accept: SUPPORTED_IMAGE_ACCEPT,
  maxFiles: MAX_CHAT_IMAGES,
  maxFileBytes: MAX_CHAT_IMAGE_BYTES,
  maxTotalBytes: MAX_CHAT_TOTAL_BYTES,
  fileLabel: '图片',
  rules: [
    (file) =>
      SUPPORTED_IMAGE_MIME_SET.has(file.type)
        ? null
        : {
            code: 'UNSUPPORTED_TYPE',
            message: `${file.name}：仅支持 PNG/JPEG/WebP/GIF/BMP`,
            fileName: file.name,
          },
  ],
  messages: {
    tooMany: (maxFiles) => `单次最多上传 ${maxFiles} 张图片`,
    empty: (file) => `${file.name}：文件内容为空`,
    tooLarge: (file, maxFileBytes) =>
      `${file.name}：单张图片不能超过 ${(maxFileBytes / 1024 / 1024).toFixed(0)}MB`,
    totalTooLarge: (file, maxTotalBytes) =>
      `${file.name}：图片总大小不能超过 ${(maxTotalBytes / 1024 / 1024).toFixed(0)}MB`,
  },
})
