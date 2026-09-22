import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/stores/auth', () => ({
  useAuthStore: Object.assign(
    () => ({ user: { user_id: 'u1', name: '测试学生', role: 'student' } }),
    { getState: () => ({ hydrate: vi.fn() }) },
  ),
}))
vi.mock('@/stores/session', () => ({
  useSessionStore: () => ({
    sessions: [],
    activeSessionId: 's1',
    setUser: vi.fn(),
    setSessions: vi.fn(),
    newSession: vi.fn(() => 's1'),
    setActive: vi.fn(),
    removeSession: vi.fn(),
  }),
}))
vi.mock('@/lib/api', () => ({
  api: {
    listSessions: vi.fn(async () => ({ sessions: [] })),
    sessionMessages: vi.fn(async () => ({ messages: [] })),
    uploadChatImages: vi.fn(),
    deleteChatImage: vi.fn(),
    chatStreamWithRetry: vi.fn(),
    closeSession: vi.fn(),
    renameSession: vi.fn(),
  },
}))
vi.mock('@/lib/api/useNotify', () => ({
  useNotify: () => ({ toast: { warning: vi.fn(), error: vi.fn(), success: vi.fn() } }),
}))

describe('ChatPage image upload', () => {
  it('renders the image upload button for a logged-in user', async () => {
    const { default: ChatPage } = await import('@/app/(main)/chat/page')
    render(<ChatPage />)
    expect(screen.getByRole('button', { name: /图片/ })).toBeDefined()
  }, 15000)

  it('does not duplicate streamed tokens under React StrictMode', async () => {
    const { api } = await import('@/lib/api')
    vi.mocked(api.chatStreamWithRetry).mockImplementation(async function* () {
      yield { event: 'text', data: { token: '甲', session_id: 's1' } }
      yield { event: 'text', data: { token: '乙', session_id: 's1' } }
      yield {
        event: 'done',
        data: {
          reply: '甲乙',
          messages_count: 2,
          session_id: 's1',
          usage: { input_tokens: 1, output_tokens: 2 },
          latency_ms: 10,
        },
      }
    })

    const { default: ChatPage } = await import('@/app/(main)/chat/page')
    const { container } = render(
      <StrictMode>
        <ChatPage />
      </StrictMode>,
    )

    const textarea = screen.getByPlaceholderText(/当前会话继续对话/)
    fireEvent.change(textarea, {
      target: { value: '测试流式输出' },
    })
    await waitFor(() => expect((textarea as HTMLTextAreaElement).value).toBe('测试流式输出'))
    fireEvent.click(screen.getByRole('button', { name: /发送/ }))
    await waitFor(() => expect(api.chatStreamWithRetry).toHaveBeenCalledTimes(1))

    await waitFor(() => expect(container.textContent).toContain('甲'))
    expect(screen.queryByText('甲甲乙乙')).toBeNull()
  }, 15000)
})
