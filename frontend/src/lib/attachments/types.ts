export interface AttachmentValidationIssue {
  code: string
  message: string
  fileName?: string
}

export interface AttachmentValidationContext {
  /** 当前已经存在的附件数量，用于增量选择校验。 */
  currentCount?: number
  /** 当前已经存在的附件总字节数。 */
  currentBytes?: number
}

export interface AttachmentValidationState {
  acceptedCount: number
  acceptedBytes: number
}

export interface AttachmentValidationResult {
  accepted: File[]
  rejected: File[]
  issues: AttachmentValidationIssue[]
}

export type AttachmentValidationRule = (
  file: File,
  context: AttachmentValidationContext,
  state: AttachmentValidationState,
) => AttachmentValidationIssue | null

export interface AttachmentValidationMessages {
  tooMany?: (maxFiles: number, fileLabel: string) => string
  empty?: (file: File, fileLabel: string) => string
  tooLarge?: (file: File, maxFileBytes: number, fileLabel: string) => string
  totalTooLarge?: (file: File, maxTotalBytes: number, fileLabel: string) => string
}

export interface AttachmentValidationConfig {
  kind: string
  accept: string
  maxFiles: number
  maxFileBytes: number
  maxTotalBytes?: number
  fileLabel?: string
  rules?: AttachmentValidationRule[]
  messages?: AttachmentValidationMessages
}

export interface ChatAttachmentValidationStrategy {
  kind: string
  accept: string
  maxFiles: number
  validate(
    files: File[],
    context?: AttachmentValidationContext,
  ): AttachmentValidationResult
}
