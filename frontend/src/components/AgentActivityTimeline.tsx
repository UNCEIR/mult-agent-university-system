'use client'

import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons'
import { Space, Typography } from 'antd'

const { Text } = Typography

const TOOL_LABELS: Record<string, string> = {
  image_recognize: '图片识别',
  adaptive_knowledge_retrieve: '知识库检索',
  query_handbook: '知识库检索',
  query_transcript: '成绩单检索',
  web_search: '联网搜索',
  image_generate: '图片生成',
  recommend_courses: '课程推荐',
  task: '子任务处理',
}

const TOOL_START_LABELS: Record<string, string> = {
  image_recognize: '正在识别图片',
  adaptive_knowledge_retrieve: '正在检索知识库',
  query_handbook: '正在检索知识库',
  query_transcript: '正在查询成绩单',
  web_search: '正在联网搜索',
  image_generate: '正在生成图片',
  recommend_courses: '正在生成课程推荐',
  task: '正在处理子任务',
}

export interface ToolActivity {
  name: string
  status: 'start' | 'end'
  tool_call_id?: string
  run_id?: string
  ok?: boolean
  code?: string
  message?: string
  retryable?: boolean
  latency_ms?: number | null
}

interface Props {
  tools: ToolActivity[]
}

function friendlyName(name: string): string {
  return TOOL_LABELS[name] || '工具处理'
}

function activityText(tool: ToolActivity): string {
  if (tool.status === 'start') {
    return TOOL_START_LABELS[tool.name] || `${friendlyName(tool.name)}进行中`
  }
  if (tool.ok === false) {
    return tool.message || `${friendlyName(tool.name)}暂不可用，请重试`
  }
  return `${friendlyName(tool.name)}已完成`
}

function displayName(tool: ToolActivity): string {
  if (tool.status === 'start') return activityText(tool)
  return friendlyName(tool.name)
}

export default function AgentActivityTimeline({ tools }: Props) {
  if (!tools || tools.length === 0) return null

  const hasActive = tools.some((tool) => tool.status === 'start')

  return (
    <details
      open={hasActive}
      style={{
        marginTop: 8,
        border: '1px solid #E4EDF7',
        borderRadius: 8,
        background: '#FAFCFF',
        padding: '6px 9px',
      }}
    >
      <summary
        aria-live="polite"
        style={{ cursor: 'pointer', color: '#5D7895', fontSize: 12, userSelect: 'none' }}
      >
        {hasActive ? '正在执行处理步骤' : `执行过程 ${tools.length} 项`}
      </summary>
      <Space orientation="vertical" size={4} style={{ width: '100%', marginTop: 6 }}>
        {tools.map((tool, index) => {
          const failed = tool.status === 'end' && tool.ok === false
          const completed = tool.status === 'end' && tool.ok !== false
          const icon = failed ? (
            <CloseCircleOutlined style={{ color: '#CF4E4E' }} />
          ) : completed ? (
            <CheckCircleOutlined style={{ color: '#1FA88D' }} />
          ) : (
            <LoadingOutlined style={{ color: '#2E6FBF' }} />
          )
          return (
            <Space
              key={tool.tool_call_id || tool.run_id || `${tool.name}-${index}`}
              size={6}
              align="start"
              style={{ width: '100%' }}
            >
              {icon}
              {completed ? (
                <>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    {displayName(tool)}
                  </Text>
                  <Text type="secondary" style={{ fontSize: 12 }}>
                    已完成
                  </Text>
                </>
              ) : (
                <Text
                  type={failed ? 'danger' : 'secondary'}
                  style={{ fontSize: 12, wordBreak: 'break-word' }}
                >
                  {activityText(tool)}
                </Text>
              )}
            </Space>
          )
        })}
      </Space>
    </details>
  )
}
