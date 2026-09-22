import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import AgentActivityTimeline from '@/components/AgentActivityTimeline'

// Chat 工具过程只展示用户可读状态，不泄露内部函数名和原始结果。

describe('AgentActivityTimeline', () => {
  it('renders nothing when no tools', () => {
    const { container } = render(<AgentActivityTimeline tools={[]} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders friendly Chinese status without internal names or raw results', () => {
    const { container } = render(
      <AgentActivityTimeline tools={[{ name: 'image_recognize', status: 'end', ok: true }]} />,
    )
    expect(screen.getByText('图片识别')).toBeDefined()
    expect(screen.getByText('已完成')).toBeDefined()
    expect(container.textContent).not.toContain('image_recognize')
    expect(container.textContent).not.toContain('observe')
    expect(container.textContent).not.toContain('{')
  })

  it('renders an in-progress friendly label', () => {
    const { container } = render(
      <AgentActivityTimeline tools={[{ name: 'adaptive_knowledge_retrieve', status: 'start' }]} />,
    )
    expect(screen.getByText('正在检索知识库')).toBeDefined()
    expect(container.textContent).not.toContain('adaptive_knowledge_retrieve')
    expect(container.textContent).not.toContain('act')
  })

  it('renders a friendly failure message without the internal code', () => {
    const { container } = render(
      <AgentActivityTimeline
        tools={[
          {
            name: 'image_recognize',
            status: 'end',
            ok: false,
            code: 'TOOL_TIMEOUT',
            message: '图片识别超时',
          },
        ]}
      />,
    )
    expect(screen.getByText('图片识别超时')).toBeDefined()
    expect(container.textContent).not.toContain('TOOL_TIMEOUT')
  })
})
