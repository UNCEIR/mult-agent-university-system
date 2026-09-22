'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button, Card, Dropdown, Empty, Input, Space, Spin, Tag, Typography, Upload } from 'antd'
import {
  SendOutlined,
  RobotOutlined,
  UserOutlined,
  ReloadOutlined,
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  MoreOutlined,
  PictureOutlined,
  CloseCircleOutlined,
} from '@ant-design/icons'
import { api } from '../../../lib/api'
import { useAuthStore } from '../../../stores/auth'
import { useSessionStore } from '../../../stores/session'
import { useNotify } from '../../../lib/api/useNotify'
import AgentActivityTimeline, { type ToolActivity } from '../../../components/AgentActivityTimeline'
import MarkdownContent from '../../../components/MarkdownContent'
import { upsertToolActivity } from '../../../lib/toolActivity'
import type { AgentTreeNode } from '../../../types/sse'
import type { ChatImageAttachment } from '../../../types'
import { getChatAttachmentValidationStrategy } from '../../../lib/attachments'
import { parseHistoryAttachments } from '../../../lib/chatImages'

const { TextArea } = Input
const { Text } = Typography
const IMAGE_ATTACHMENT_STRATEGY = getChatAttachmentValidationStrategy('image')

interface ChatItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  tools: ToolActivity[]
  usage?: Record<string, unknown>
  latency_ms?: number | null
  agentTree?: AgentTreeNode[]
  attachments?: ChatImageAttachment[]
  error?: string
}

let chatItemSequence = 0

function nextChatItemId(role: ChatItem['role']): string {
  chatItemSequence += 1
  return `${role}-${Date.now()}-${chatItemSequence}`
}

export default function ChatPage() {
  const router = useRouter()
  const { user } = useAuthStore()
  const sessionStore = useSessionStore()
  const notify = useNotify()
  const [items, setItems] = useState<ChatItem[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [pendingImages, setPendingImages] = useState<ChatImageAttachment[]>([])
  const [uploadingImages, setUploadingImages] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const uploadingRef = useRef(false)
  const streamAssistantIdRef = useRef<string | null>(null)
  const tokenBufferRef = useRef('')
  const tokenRafRef = useRef<number | null>(null)

  // 登录态 → 初始化会话 store 并刷新列表
  useEffect(() => {
    useAuthStore.getState().hydrate()
  }, [])
  useEffect(() => {
    if (user?.user_id) {
      sessionStore.setUser(user.user_id)
      refreshSessions()
    }
    // 仅依赖 user_id；refreshSessions / sessionStore 来自 store，引用稳定
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id])

  const refreshSessions = useCallback(async () => {
    if (!user?.user_id) return
    try {
      const res = await api.listSessions(user.user_id)
      sessionStore.setSessions(res.sessions)
    } catch {
      // 列表失败不阻塞对话
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id])

  // 切换/进入会话 → 回显历史消息
  useEffect(() => {
    if (!user?.user_id || !sessionStore.activeSessionId) return
    let cancelled = false
    setLoadingHistory(true)
    setItems([])
    api
      .sessionMessages(sessionStore.activeSessionId, user.user_id)
      .then((res) => {
        if (cancelled) return
        const restored: ChatItem[] = []
        for (const m of res.messages) {
          if (m.role === 'user') {
            restored.push({
              id: nextChatItemId('user'),
              role: 'user',
              content: m.content ?? '',
              tools: [],
              attachments: parseHistoryAttachments(m.attachments_json),
            })
          } else if (m.role === 'assistant') {
            restored.push({ id: nextChatItemId('assistant'), role: 'assistant', content: m.content ?? '', tools: [] })
          }
        }
        setItems(restored)
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setLoadingHistory(false)
      })
    return () => {
      cancelled = true
    }
  }, [sessionStore.activeSessionId, user?.user_id])

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [items])

  const flushTokenBuffer = useCallback(() => {
    if (tokenRafRef.current !== null) {
      cancelAnimationFrame(tokenRafRef.current)
      tokenRafRef.current = null
    }
    const assistantId = streamAssistantIdRef.current
    const buffered = tokenBufferRef.current
    tokenBufferRef.current = ''
    if (!assistantId || !buffered) return

    setItems((prev) =>
      prev.map((item) =>
        item.id === assistantId
          ? { ...item, content: item.content + buffered }
          : item,
      ),
    )
  }, [])

  const scheduleTokenFlush = useCallback(() => {
    if (tokenRafRef.current !== null) return
    tokenRafRef.current = requestAnimationFrame(flushTokenBuffer)
  }, [flushTokenBuffer])

  useEffect(
    () => () => {
      if (tokenRafRef.current !== null) cancelAnimationFrame(tokenRafRef.current)
      tokenRafRef.current = null
      tokenBufferRef.current = ''
      streamAssistantIdRef.current = null
    },
    [],
  )

  const handleNewSession = () => {
    sessionStore.newSession()
    setItems([])
  }

  const handleSwitch = (sessionId: string) => {
    sessionStore.setActive(sessionId)
  }

  const handleDelete = async (sessionId: string) => {
    if (!user?.user_id) return
    try {
      await api.closeSession(sessionId, user.user_id)
      sessionStore.removeSession(sessionId)
      notify.toast.success('会话已删除')
    } catch (e: unknown) {
      notify.toast.error(e, '删除失败')
    }
  }

  const handleRename = async (sessionId: string) => {
    const title = renameValue.trim()
    if (!title || !user?.user_id) return
    try {
      await api.renameSession(sessionId, user.user_id, title)
      setRenamingId(null)
      refreshSessions()
      notify.toast.success('已重命名')
    } catch (e: unknown) {
      notify.toast.error(e, '重命名失败')
    }
  }

  const handleImageFiles = async (files: File[]) => {
    if (!files.length || uploadingRef.current || uploadingImages || streaming) return
    if (!user?.user_id) {
      notify.toast.warning('请先登录')
      router.push('/login')
      return
    }
    const validation = IMAGE_ATTACHMENT_STRATEGY.validate(files, {
      currentCount: pendingImages.length,
      currentBytes: pendingImages.reduce((sum, image) => sum + (image.file_size ?? 0), 0),
    })
    if (validation.issues.length) {
      notify.toast.warning(validation.issues.map((issue) => issue.message).join('；'))
    }
    if (!validation.accepted.length) return

    const sessionId = sessionStore.activeSessionId ?? sessionStore.newSession()
    if (sessionStore.activeSessionId == null) sessionStore.setActive(sessionId)
    uploadingRef.current = true
    setUploadingImages(true)
    try {
      const result = await api.uploadChatImages(validation.accepted, sessionId, user.user_id)
      setPendingImages((prev) =>
        [...prev, ...result.images].slice(0, IMAGE_ATTACHMENT_STRATEGY.maxFiles),
      )
      notify.toast.success(`已上传 ${result.images.length} 张图片`)
    } catch (e: unknown) {
      notify.toast.error(e, '图片上传失败')
    } finally {
      setUploadingImages(false)
      uploadingRef.current = false
    }
  }

  const handleRemoveImage = (imageId: string) => {
    const target = pendingImages.find((image) => image.image_id === imageId)
    setPendingImages((prev) => prev.filter((image) => image.image_id !== imageId))
    if (user?.user_id && target) void api.deleteChatImage(imageId, user.user_id, sessionStore.activeSessionId ?? 'default').catch(() => {})
  }
  const handleSend = useCallback(
    async (retryContent?: string) => {
      const content = (retryContent ?? input).trim()
      if ((!content && pendingImages.length === 0) || streaming) return
      if (!user?.user_id) {
        notify.toast.warning('请先登录')
        router.push('/login')
        return
      }
      // 无会话则新建
      const sessionId = sessionStore.activeSessionId ?? sessionStore.newSession()
      if (sessionStore.activeSessionId == null) {
        sessionStore.setActive(sessionId)
      }
      const selectedImages = pendingImages
      const userItemId = nextChatItemId('user')
      const assistantItemId = nextChatItemId('assistant')
      streamAssistantIdRef.current = assistantItemId
      tokenBufferRef.current = ''
      setInput('')
      setPendingImages([])
      setStreaming(true)
      setItems((prev) => [
        ...prev,
        { id: userItemId, role: 'user', content: content || '[图片]', tools: [], attachments: selectedImages },
        { id: assistantItemId, role: 'assistant', content: '', tools: [] },
      ])

      const ac = new AbortController()
      const body = {
        message: content || '请分析我上传的图片',
        session_id: sessionId,
        user_id: user.user_id,
        image_ids: selectedImages.map((image) => image.image_id),
      }
      try {
        for await (const evt of api.chatStreamWithRetry(body, ac.signal)) {
          if (evt.event === 'text') {
            tokenBufferRef.current += evt.data.token
            scheduleTokenFlush()
          } else if (evt.event === 'tool') {
            setItems((prev) => {
              const assistantId = streamAssistantIdRef.current
              return prev.map((item) =>
                item.id === assistantId
                  ? { ...item, tools: upsertToolActivity(item.tools, evt.data) }
                  : item,
              )
            })
          } else if (evt.event === 'done') {
            flushTokenBuffer()
            setItems((prev) => {
              const assistantId = streamAssistantIdRef.current
              return prev.map((item) =>
                item.id === assistantId
                  ? {
                      ...item,
                      usage: evt.data.usage,
                      latency_ms: evt.data.latency_ms,
                      ...(evt.data.agent_tree ? { agentTree: evt.data.agent_tree } : {}),
                    }
                  : item,
              )
            })
          } else if (evt.event === 'error') {
            flushTokenBuffer()
            setItems((prev) => {
              const assistantId = streamAssistantIdRef.current
              return prev.map((item) =>
                item.id === assistantId
                  ? { ...item, error: `${evt.data.code}: ${evt.data.message}` }
                  : item,
              )
            })
          }
        }
        refreshSessions()
      } catch (e: unknown) {
        setItems((prev) => {
          const assistantId = streamAssistantIdRef.current
          return prev.map((item) =>
            item.id === assistantId
              ? { ...item, error: e instanceof Error ? e.message : '请求失败' }
              : item,
          )
        })
      } finally {
        flushTokenBuffer()
        setStreaming(false)
      }
    },
    // zustand store 对象引用稳定，不参与依赖；refreshSessions / notify.toast 同理
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      input,
      streaming,
      pendingImages,
      uploadingImages,
      user?.user_id,
      sessionStore.activeSessionId,
      refreshSessions,
      flushTokenBuffer,
      scheduleTokenFlush,
      router,
      notify.toast,
    ],
  )

  if (!user) {
    return (
      <Card
        className="glass-card"
        style={{ maxWidth: 480, margin: '80px auto', borderRadius: 16 }}
        styles={{ body: { padding: 40, textAlign: 'center' } }}
      >
        <Empty description="登录后开始智能对话，会话将持久保存" />
        <Button type="primary" style={{ marginTop: 12 }} onClick={() => router.push('/login')}>
          去登录
        </Button>
      </Card>
    )
  }

  return (
    <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
      {/* 左侧会话栏 */}
      <Card
        className="glass-card"
        style={{ width: 260, flexShrink: 0, borderRadius: 14 }}
        styles={{ body: { padding: 12 } }}
      >
        <Button
          type="primary"
          block
          icon={<PlusOutlined />}
          onClick={handleNewSession}
          style={{ marginBottom: 12 }}
        >
          新对话
        </Button>
        <div style={{ maxHeight: '62vh', overflowY: 'auto' }}>
          {sessionStore.sessions.length === 0 ? (
            <div style={{ padding: 16, textAlign: 'center' }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                暂无历史会话
              </Text>
            </div>
          ) : (
            <ul aria-label="历史会话" style={{ margin: 0, padding: 0, listStyle: 'none' }}>
              {sessionStore.sessions.map((s) => (
                <li
                  key={s.session_id}
                  onClick={() => handleSwitch(s.session_id)}
                  style={{
                    padding: '8px 10px',
                    borderRadius: 8,
                    cursor: 'pointer',
                    marginBottom: 4,
                    listStyle: 'none',
                    background:
                      s.session_id === sessionStore.activeSessionId ? '#EAF2FB' : 'transparent',
                    border:
                      s.session_id === sessionStore.activeSessionId
                        ? '1px solid #CFE3F5'
                        : '1px solid transparent',
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      gap: 6,
                    }}
                  >
                    {renamingId === s.session_id ? (
                      <Input
                        size="small"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onPressEnter={() => handleRename(s.session_id)}
                        onBlur={() => setRenamingId(null)}
                        onClick={(e) => e.stopPropagation()}
                        autoFocus
                      />
                    ) : (
                      <Text
                        ellipsis
                        style={{
                          fontSize: 13,
                          color:
                            s.session_id === sessionStore.activeSessionId ? '#2E6FBF' : '#33475C',
                          fontWeight: 500,
                        }}
                      >
                        {s.display_title || s.title || '新对话'}
                      </Text>
                    )}
                    <Space size={0} onClick={(e) => e.stopPropagation()}>
                      <Dropdown
                        menu={{
                          items: [
                            {
                              key: 'rename',
                              icon: <EditOutlined />,
                              label: '重命名',
                              onClick: () => {
                                setRenamingId(s.session_id)
                                setRenameValue(s.display_title || s.title || '')
                              },
                            },
                            {
                              key: 'delete',
                              icon: <DeleteOutlined />,
                              label: '删除',
                              danger: true,
                              onClick: () => handleDelete(s.session_id),
                            },
                          ],
                        }}
                      >
                        <Button
                          type="text"
                          size="small"
                          icon={<MoreOutlined />}
                          style={{ fontSize: 11 }}
                        />
                      </Dropdown>
                    </Space>
                  </div>
                  <Text type="secondary" style={{ fontSize: 11 }}>
                    {s.message_count} 条消息
                  </Text>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      {/* 主对话区 */}
      <Card
        className="glass-card"
        style={{ flex: 1, minWidth: 0, borderRadius: 14 }}
        styles={{ body: { padding: 20 } }}
        title={
          <Space>
            <RobotOutlined style={{ color: '#2E6FBF' }} />
            <span className="serif-heading" style={{ fontSize: 15 }}>
              智能对话
            </span>
            <Text type="secondary" style={{ fontSize: 12 }}>
              知识库问答 / 课程推荐 / 写作 / 搜索 / 图片生成 · 会话跨页面持久
            </Text>
          </Space>
        }
      >
        <div
          ref={scrollRef}
          style={{ minHeight: 420, maxHeight: '56vh', overflowY: 'auto', paddingBottom: 16 }}
        >
          {loadingHistory ? (
            <div style={{ textAlign: 'center', padding: 60 }}>
              <Spin />
            </div>
          ) : items.length === 0 && !streaming ? (
            <Empty description="输入你想问的问题，例如：我适合选哪些公选课？奖学金申请条件是什么？" />
          ) : (
            items.map((item, idx) => (
              <div
                key={idx}
                style={{
                  marginBottom: 16,
                  display: 'flex',
                  gap: 10,
                  justifyContent: item.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                <div
                  style={{
                    maxWidth: '82%',
                    padding: '10px 14px',
                    borderRadius: 12,
                    background: item.role === 'user' ? '#EAF2FB' : '#F2F7FD',
                    border: '1px solid #CFE3F5',
                  }}
                >
                  <Space size={6} style={{ marginBottom: 6 }}>
                    {item.role === 'user' ? (
                      <UserOutlined style={{ color: '#2E6FBF' }} />
                    ) : (
                      <RobotOutlined style={{ color: '#14B8A6' }} />
                    )}
                    <Text strong style={{ fontSize: 12 }}>
                      {item.role === 'user' ? '我' : '助手'}
                    </Text>
                  </Space>
                  {item.role === 'user' ? (
                    <div>
                      {item.attachments && item.attachments.length > 0 && (
                        <Space size={6} wrap style={{ marginBottom: item.content ? 8 : 0 }}>
                          {item.attachments.map((image) => (
                            // eslint-disable-next-line @next/next/no-img-element
                            <img
                              key={image.image_id}
                              src={image.preview_url}
                              alt={image.filename || '聊天图片'}
                              style={{
                                width: 96,
                                height: 72,
                                objectFit: 'cover',
                                borderRadius: 8,
                                border: '1px solid #CFE3F5',
                              }}
                            />
                          ))}
                        </Space>
                      )}
                      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', color: '#33475C' }}>
                        {item.content}
                      </div>
                    </div>
                  ) : (
                    <MarkdownContent content={item.content} />
                  )}
                  {item.tools.length > 0 && <AgentActivityTimeline tools={item.tools} />}
                  {item.agentTree && item.agentTree.length > 0 && (
                    <Space size={4} wrap style={{ marginTop: 4 }}>
                      {item.agentTree.map((n, i) => (
                        <Tag key={i} icon={<RobotOutlined />} color="geekblue">
                          {n.name} · {n.status}
                        </Tag>
                      ))}
                    </Space>
                  )}
                  {item.usage && (
                    <Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
                      耗时 {item.latency_ms ? `${(item.latency_ms / 1000).toFixed(1)}s` : '—'}
                    </Text>
                  )}
                  {item.error && (
                    <Space orientation="vertical" style={{ marginTop: 8 }}>
                      <Text type="danger" style={{ fontSize: 12 }}>
                        {item.error}
                      </Text>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        onClick={() => handleSend(item.content)}
                        disabled={streaming}
                      >
                        重试
                      </Button>
                    </Space>
                  )}
                </div>
              </div>
            ))
          )}
          {streaming && <Spin size="small" style={{ marginLeft: 12 }} />}
        </div>

        {pendingImages.length > 0 && (
          <Space size={8} wrap style={{ marginTop: 10 }}>
            {pendingImages.map((image) => (
              <div key={image.image_id} style={{ position: 'relative' }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={image.preview_url}
                  alt={image.filename || '待发送图片'}
                  style={{
                    width: 72,
                    height: 56,
                    objectFit: 'cover',
                    borderRadius: 8,
                    border: '1px solid #CFE3F5',
                  }}
                />
                <Button
                  type="text"
                  size="small"
                  aria-label={`移除 ${image.filename || image.image_id}`}
                  icon={<CloseCircleOutlined />}
                  onClick={() => handleRemoveImage(image.image_id)}
                  disabled={streaming}
                  style={{ position: 'absolute', top: -8, right: -8, background: '#fff' }}
                />
              </div>
            ))}
          </Space>
        )}

        <div style={{ display: 'flex', gap: 8, width: '100%', marginTop: 8, alignItems: 'flex-end' }}>
          <Upload
            accept={IMAGE_ATTACHMENT_STRATEGY.accept}
            multiple
            showUploadList={false}
            beforeUpload={(file, fileList) => {
              if (file.uid === fileList[0]?.uid) {
                void handleImageFiles(fileList as unknown as File[])
              }
              return Upload.LIST_IGNORE
            }}
            disabled={
              streaming ||
              uploadingImages ||
              pendingImages.length >= IMAGE_ATTACHMENT_STRATEGY.maxFiles
            }
          >
            <Button
              icon={<PictureOutlined />}
              loading={uploadingImages}
              disabled={streaming || pendingImages.length >= IMAGE_ATTACHMENT_STRATEGY.maxFiles}
              title="上传图片：PNG/JPEG/WebP/GIF/BMP"
            >
              图片
            </Button>
          </Upload>
          <TextArea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onPaste={(e) => {
              const files = Array.from(e.clipboardData.files)
              if (files.length) {
                e.preventDefault()
                void handleImageFiles(files)
              }
            }}
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                handleSend()
              }
            }}
            placeholder={`${sessionStore.activeSessionId ? '当前会话继续对话' : '新会话'} · Enter 发送 / Shift+Enter 换行`}
            autoSize={{ minRows: 1, maxRows: 4 }}
            disabled={streaming}
            style={{ flex: 1 }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={() => handleSend()}
            loading={streaming}
            disabled={uploadingImages}
          >
            发送
          </Button>
        </div>
      </Card>
    </div>
  )
}
