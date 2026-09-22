import { imageAttachmentValidationStrategy } from './image'
import type { ChatAttachmentValidationStrategy } from './types'

export * from './image'
export * from './types'
export * from './validation'

const CHAT_ATTACHMENT_STRATEGIES: Record<string, ChatAttachmentValidationStrategy> = {
  image: imageAttachmentValidationStrategy,
}

export function getChatAttachmentValidationStrategy(
  kind: string,
): ChatAttachmentValidationStrategy {
  const strategy = CHAT_ATTACHMENT_STRATEGIES[kind]
  if (!strategy) {
    throw new Error(`Unknown chat attachment kind: ${kind}`)
  }
  return strategy
}
