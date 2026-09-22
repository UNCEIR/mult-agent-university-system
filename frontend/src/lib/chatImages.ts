import { imageAttachmentValidationStrategy } from './attachments'

export {
  MAX_CHAT_IMAGES,
  MAX_CHAT_IMAGE_BYTES,
  MAX_CHAT_TOTAL_BYTES,
  SUPPORTED_IMAGE_ACCEPT,
  SUPPORTED_IMAGE_MIME_TYPES,
} from './attachments'

export interface ImageValidation {
  accepted: File[]
  errors: string[]
}

/** @deprecated 新代码请直接使用 imageAttachmentValidationStrategy。 */
export function validateImageSelection(
  files: File[],
  currentCount = 0,
  currentBytes = 0,
): ImageValidation {
  const result = imageAttachmentValidationStrategy.validate(files, {
    currentCount,
    currentBytes,
  })
  return {
    accepted: result.accepted,
    errors: result.issues.map((issue) => issue.message),
  }
}

export function parseHistoryAttachments(value: unknown): import('../types').ChatImageAttachment[] {
  if (!value) return []
  let items: unknown = value
  if (typeof value === 'string') {
    try {
      items = JSON.parse(value)
    } catch {
      return []
    }
  }
  if (!Array.isArray(items)) return []
  return items.filter(
    (item): item is import('../types').ChatImageAttachment =>
      !!item &&
      typeof item === 'object' &&
      typeof (item as { image_id?: unknown }).image_id === 'string' &&
      typeof (item as { preview_url?: unknown }).preview_url === 'string',
  )
}
