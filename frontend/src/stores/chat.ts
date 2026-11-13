import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { chat } from '@/api/client'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import type { ResponseType, SourceRef } from '@/types/api'

export type GenerateStage = 'parsing' | 'retrieving' | 'optimizing' | 'explaining'

export interface ChatMessage {
  role: 'user' | 'system'
  content: string
  response_type: ResponseType | null
  sources: SourceRef[] | null
  plan_refs: string[] | null
}

const STAGES: GenerateStage[] = ['parsing', 'retrieving', 'optimizing', 'explaining']
export const STAGE_LABELS: Record<GenerateStage, string> = {
  parsing: '正在理解您的需求…',
  retrieving: '正在查询实时数据…',
  optimizing: '正在优化三套方案…',
  explaining: '正在生成解释…',
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<ChatMessage[]>([])
  const pendingStage = ref<GenerateStage | null>(null)
  const lastFailedInput = ref<string | null>(null)

  const busy = computed(() => pendingStage.value !== null)

  let stageTimer: number | undefined

  function startStages(): void {
    let index = 0
    pendingStage.value = STAGES[0] ?? null
    stageTimer = window.setInterval(() => {
      index = Math.min(index + 1, STAGES.length - 1)
      pendingStage.value = STAGES[index] ?? null
    }, 3_000)
  }

  function stopStages(): void {
    if (stageTimer !== undefined) {
      window.clearInterval(stageTimer)
      stageTimer = undefined
    }
    pendingStage.value = null
  }

  async function send(text: string, options: { silentUser?: boolean } = {}): Promise<void> {
    const trimmed = text.trim()
    if (!trimmed || busy.value) return
    if (!options.silentUser) {
      messages.value.push({ role: 'user', content: trimmed, response_type: null, sources: null, plan_refs: null })
    }
    lastFailedInput.value = null
    startStages()
    const sessionStore = useSessionStore()
    try {
      const response = await chat({
        conversation_id: sessionStore.activeConversationId,
        message: trimmed,
      })
      sessionStore.bindNewConversation(response.conversation_id, trimmed.slice(0, 50))
      messages.value.push({
        role: 'system',
        content: response.content,
        response_type: response.response_type,
        sources: response.sources,
        plan_refs: response.plans ? response.plans.map((p) => p.plan_id) : null,
      })
      if (response.response_type === 'plans' && response.plans && response.plans.length > 0) {
        usePlanStore().loadPlans(response.plans, response.data_time)
      }
      if (response.code === 50003) {
        lastFailedInput.value = trimmed
      }
    } catch (error) {
      lastFailedInput.value = trimmed
      messages.value.push({
        role: 'system',
        content: error instanceof Error ? error.message : '服务繁忙，请重试',
        response_type: 'fallback',
        sources: null,
        plan_refs: null,
      })
    } finally {
      stopStages()
    }
  }

  function resend(): void {
    const text = lastFailedInput.value
    if (!text) return
    const failedUserMessage = [...messages.value].reverse().find((m) => m.role === 'user' && m.content === text)
    if (failedUserMessage) {
      const index = messages.value.lastIndexOf(failedUserMessage)
      if (index >= 0) messages.value.splice(index, 1)
    }
    const failedFallback = [...messages.value].reverse().find((m) => m.role === 'system' && m.response_type === 'fallback')
    if (failedFallback) {
      const index = messages.value.lastIndexOf(failedFallback)
      if (index >= 0) messages.value.splice(index, 1)
    }
    void send(text, { silentUser: false })
  }

  async function loadHistory(conversationId: string): Promise<void> {
    const planStore = usePlanStore()
    messages.value = []
    planStore.reset()
    const { listMessages } = await import('@/api/client')
    const result = await listMessages(conversationId, 1, 100)
    messages.value = result.items.map((m) => ({
      role: m.role,
      content: m.content,
      response_type: m.response_type,
      sources: null,
      plan_refs: m.plan_refs,
    }))
    const lastWithPlans = [...messages.value].reverse().find((m) => m.plan_refs && m.plan_refs.length > 0)
    if (lastWithPlans?.plan_refs) {
      await planStore.restoreFromRefs(lastWithPlans.plan_refs)
    }
  }

  function reset(): void {
    messages.value = []
    lastFailedInput.value = null
    stopStages()
  }

  return { messages, pendingStage, lastFailedInput, busy, send, resend, loadHistory, reset }
})