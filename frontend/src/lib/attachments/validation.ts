import type {
  AttachmentValidationConfig,
  AttachmentValidationContext,
  AttachmentValidationIssue,
  AttachmentValidationResult,
  AttachmentValidationState,
  ChatAttachmentValidationStrategy,
} from './types'

function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(2)}MB`
}

function defaultMessages(config: AttachmentValidationConfig) {
  return {
    tooMany: (maxFiles: number, fileLabel: string) =>
      `单次最多上传 ${maxFiles} 个${fileLabel}`,
    empty: (file: File, fileLabel: string) => `${file.name}：${fileLabel}内容为空`,
    tooLarge: (file: File, maxFileBytes: number, fileLabel: string) =>
      `${file.name}：单个${fileLabel}不能超过 ${megabytes(maxFileBytes)}`,
    totalTooLarge: (file: File, maxTotalBytes: number, fileLabel: string) =>
      `${file.name}：${fileLabel}总大小不能超过 ${megabytes(maxTotalBytes)}`,
    ...config.messages,
  }
}

export function validateAttachmentFiles(
  config: AttachmentValidationConfig,
  files: File[],
  context: AttachmentValidationContext = {},
): AttachmentValidationResult {
  const accepted: File[] = []
  const rejected: File[] = []
  const issues: AttachmentValidationIssue[] = []
  const state: AttachmentValidationState = {
    acceptedCount: Math.max(0, context.currentCount ?? 0),
    acceptedBytes: Math.max(0, context.currentBytes ?? 0),
  }
  const fileLabel = config.fileLabel ?? '文件'
  const messages = defaultMessages(config)
  const rules = config.rules ?? []

  for (const file of files) {
    let issue: AttachmentValidationIssue | null = null

    if (state.acceptedCount >= config.maxFiles) {
      issue = {
        code: 'TOO_MANY_FILES',
        message: messages.tooMany(config.maxFiles, fileLabel),
        fileName: file.name,
      }
    } else if (file.size <= 0) {
      issue = {
        code: 'EMPTY_FILE',
        message: messages.empty(file, fileLabel),
        fileName: file.name,
      }
    } else if (file.size > config.maxFileBytes) {
      issue = {
        code: 'FILE_TOO_LARGE',
        message: messages.tooLarge(file, config.maxFileBytes, fileLabel),
        fileName: file.name,
      }
    } else if (
      config.maxTotalBytes !== undefined &&
      state.acceptedBytes + file.size > config.maxTotalBytes
    ) {
      issue = {
        code: 'TOTAL_SIZE_EXCEEDED',
        message: messages.totalTooLarge(file, config.maxTotalBytes, fileLabel),
        fileName: file.name,
      }
    } else {
      for (const rule of rules) {
        issue = rule(file, context, state)
        if (issue) break
      }
    }

    if (issue) {
      rejected.push(file)
      issues.push(issue)
      continue
    }

    accepted.push(file)
    state.acceptedCount += 1
    state.acceptedBytes += file.size
  }

  return { accepted, rejected, issues }
}

export function createAttachmentValidationStrategy(
  config: AttachmentValidationConfig,
): ChatAttachmentValidationStrategy {
  return {
    kind: config.kind,
    accept: config.accept,
    maxFiles: config.maxFiles,
    validate: (files, context) => validateAttachmentFiles(config, files, context),
  }
}
