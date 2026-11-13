<script setup lang="ts">
import type { RefreshResponse, TransportMode } from '@/types/api'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import ChatComposer from '@/components/tourist/ChatComposer.vue'
import ChangeNoticeToast from '@/components/tourist/ChangeNoticeToast.vue'
import ClarificationPanel from '@/components/tourist/ClarificationPanel.vue'
import ConversationList from '@/components/tourist/ConversationList.vue'
import GeneratingIndicator from '@/components/tourist/GeneratingIndicator.vue'
import MessageBubble from '@/components/tourist/MessageBubble.vue'
import PlanDiffDialog from '@/components/tourist/PlanDiffDialog.vue'
import PlanPanel from '@/components/tourist/PlanPanel.vue'
import type { GeoPoint } from '@/composables/useGeolocation'
import { useChatStore } from '@/stores/chat'
import { useMapStore } from '@/stores/map'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'

const sessionStore = useSessionStore()
const chatStore = useChatStore()
const planStore = usePlanStore()
const mapStore = useMapStore()

const origin = ref<GeoPoint | null>(null)
const planPanelVisible = ref(false)
const messageScrollRef = ref<HTMLDivElement | null>(null)
const narrowScreen = ref(window.innerWidth < 1280)

const clarifyMessage = computed(() => {
  const messages = chatStore.messages
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message !== undefined && message.role === 'system') {
      return message.response_type === 'clarify' ? message : null
    }
  }
  return null
})

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = messageScrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}

async function handleSelectConversation(conversationId: string): Promise<void> {
  if (chatStore.busy) return
  sessionStore.switchTo(conversationId)
  planPanelVisible.value = false
  await chatStore.loadHistory(conversationId)
  await scrollToBottom()
}

function handleCreateConversation(): void {
  sessionStore.startNew()
  chatStore.reset()
  planStore.reset()
  mapStore.reset()
  planPanelVisible.value = false
}

async function handleRemoveConversation(conversationId: string): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除会话将同时移除该会话下的全部行程记录，是否继续？',
      '删除会话确认',
      { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
    )
  } catch {
    return
  }
  const wasActive = sessionStore.activeConversationId === conversationId
  await sessionStore.remove(conversationId)
  if (wasActive) {
    chatStore.reset()
    planStore.reset()
    const nextId = sessionStore.activeConversationId
    if (nextId) await chatStore.loadHistory(nextId)
  }
}

async function handleAnswer(text: string): Promise<void> {
  await chatStore.send(text)
  await scrollToBottom()
}

function handleViewPlans(): void {
  if (narrowScreen.value) planPanelVisible.value = true
  const planId = planStore.selectedPlanId
  if (planId) void planStore.selectPlan(planId)
}

function handleModeChange(mode: TransportMode): void {
  const destination =
    planStore.selectedDetail?.items.find((item) => item.activity_type === '游览' && item.poi_id)?.poi_id ?? null
  const originText = origin.value ? `${origin.value.lng.toFixed(6)},${origin.value.lat.toFixed(6)}` : ''
  void mapStore.switchMode(mode, originText, destination ?? '')
}

function handleNoticeReview(notice: { planId: string; diff: RefreshResponse | null }): void {
  if (notice.diff) {
    planStore.pendingDiff = notice.diff
  } else {
    const active = sessionStore.activeConversationId
    if (active) void chatStore.loadHistory(active)
  }
  planStore.ackNotice(notice.planId)
  void sessionStore.refresh()
}

async function syncPendingSuggestions(): Promise<void> {
  const total = sessionStore.pendingSuggestionTotal
  if (total > 0 && planStore.notices.length === 0) {
    planStore.pushNotice({
      planId: 'pending-sync',
      reason: `您有 ${total} 条行程调整建议待处理`,
      summary: '点击查看调整建议与前后差异',
      diff: null,
    })
  }
}

async function handleRefreshConversations(): Promise<void> {
  await sessionStore.refresh()
  await syncPendingSuggestions()
}

function handleNoticeDismiss(): void {
  const first = planStore.notices[0]
  if (first) planStore.ackNotice(first.planId)
}

async function handleConfirmDiff(): Promise<void> {
  try {
    await planStore.confirmPending()
  } finally {
    await sessionStore.refresh()
  }
}

function onResize(): void {
  narrowScreen.value = window.innerWidth < 1280
}

onMounted(async () => {
  window.addEventListener('resize', onResize)
  try {
    await planStore.loadPois()
  } catch {
    /* POI 列表失败不应阻塞会话恢复，时间轴会退化为编号显示 */
  }
  try {
    await sessionStore.init()
    const active = sessionStore.activeConversationId
    if (active) await chatStore.loadHistory(active)
    await syncPendingSuggestions()
  } catch {
    /* 会话恢复失败不阻塞首屏 */
  }
  await scrollToBottom()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
})

watch(
  () => chatStore.messages.length,
  () => {
    void scrollToBottom()
  },
)
</script>

<template>
  <main class="chat-home">
    <ConversationList
      :conversations="sessionStore.conversations"
      :active-id="sessionStore.activeConversationId"
      @select="handleSelectConversation"
      @create="handleCreateConversation"
      @remove="handleRemoveConversation"
    />

    <section class="chat-column">
      <header class="chat-header">
        <div class="brand">
          <h1 class="brand-title">彭城智游</h1>
          <span class="brand-sub">徐州 AI 城市旅游智能体</span>
        </div>
        <el-button size="small" @click="handleRefreshConversations">刷新会话</el-button>
      </header>
      <div ref="messageScrollRef" class="message-flow">
        <el-empty
          v-if="chatStore.messages.length === 0 && !chatStore.busy"
          description="告诉我您的出行想法，例如：第一次来徐州，一天时间，预算300元，喜欢历史文化"
        />
        <template v-for="(message, index) in chatStore.messages" :key="index">
          <MessageBubble :message="message" @view-plans="handleViewPlans" />
          <ClarificationPanel
            v-if="message === clarifyMessage && !chatStore.busy"
            :questions="[message.content]"
            :round="1"
            @answer="handleAnswer"
          />
        </template>
        <GeneratingIndicator v-if="chatStore.pendingStage" :stage="chatStore.pendingStage" />
      </div>
      <ChatComposer />
    </section>

    <section v-if="!narrowScreen" class="plan-panel">
      <PlanPanel
        :origin="origin"
        @set-origin="origin = $event"
        @mode-change="handleModeChange"
      />
    </section>

    <el-drawer v-else v-model="planPanelVisible" title="方案面板" direction="rtl" size="440px">
      <PlanPanel
        :origin="origin"
        @set-origin="origin = $event"
        @mode-change="handleModeChange"
      />
    </el-drawer>

    <el-button
      v-if="narrowScreen && !planPanelVisible && planStore.plans.length > 0"
      class="panel-fab"
      type="primary"
      round
      @click="planPanelVisible = true"
    >
      查看方案
    </el-button>

    <PlanDiffDialog
      :diff="planStore.pendingDiff"
      :visible="planStore.pendingDiff !== null"
      :poi-map="planStore.poiMap"
      @confirm="handleConfirmDiff"
      @cancel="planStore.dismissDiff()"
    />

    <ChangeNoticeToast
      :notices="planStore.notices"
      @review="handleNoticeReview"
      @dismiss="handleNoticeDismiss"
    />
  </main>
</template>

<style scoped>
.chat-home {
  display: flex;
  height: 100%;
  overflow: hidden;
}

.chat-column {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--el-fill-color-lighter);
}

.chat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 20px;
  background: #fff;
  border-bottom: 1px solid var(--el-border-color-light);
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.brand-title {
  margin: 0;
  font-size: 18px;
}

.brand-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.message-flow {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}

.plan-panel {
  width: 420px;
  border-left: 1px solid var(--el-border-color-light);
  background: #fff;
  overflow-y: auto;
  padding: 14px;
}

.panel-fab {
  position: fixed;
  right: 20px;
  bottom: 90px;
  z-index: 1500;
}
</style>