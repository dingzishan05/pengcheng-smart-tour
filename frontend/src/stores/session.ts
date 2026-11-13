import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { deleteConversation, listConversations } from '@/api/client'
import type { ConversationItem } from '@/types/api'

export const useSessionStore = defineStore('session', () => {
  const conversations = ref<ConversationItem[]>([])
  const activeConversationId = ref<string | null>(null)
  const loaded = ref(false)

  const pendingSuggestionTotal = computed(() =>
    conversations.value.reduce((sum, c) => sum + c.pending_suggestion_count, 0),
  )

  async function refresh(): Promise<void> {
    const result = await listConversations()
    conversations.value = result.items
  }

  async function init(): Promise<void> {
    if (loaded.value) return
    await refresh()
    activeConversationId.value = conversations.value[0]?.conversation_id ?? null
    loaded.value = true
  }

  async function remove(conversationId: string): Promise<void> {
    await deleteConversation(conversationId)
    conversations.value = conversations.value.filter((c) => c.conversation_id !== conversationId)
    if (activeConversationId.value === conversationId) {
      activeConversationId.value = conversations.value[0]?.conversation_id ?? null
    }
  }

  function switchTo(conversationId: string): void {
    activeConversationId.value = conversationId
  }

  function startNew(): void {
    activeConversationId.value = null
  }

  function bindNewConversation(conversationId: string, title: string | null): void {
    if (!conversations.value.some((c) => c.conversation_id === conversationId)) {
      conversations.value.unshift({
        conversation_id: conversationId,
        title,
        status: 'active',
        last_active_at: new Date().toISOString(),
        pending_suggestion_count: 0,
      })
    }
    activeConversationId.value = conversationId
  }

  return {
    conversations,
    activeConversationId,
    loaded,
    pendingSuggestionTotal,
    init,
    refresh,
    remove,
    switchTo,
    startNew,
    bindNewConversation,
  }
})