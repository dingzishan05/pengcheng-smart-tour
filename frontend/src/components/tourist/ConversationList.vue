<script setup lang="ts">
import type { ConversationItem } from '@/types/api'
import { computed } from 'vue'

const props = defineProps<{ conversations: ConversationItem[]; activeId: string | null }>()

const emit = defineEmits<{
  (e: 'select', conversationId: string): void
  (e: 'create'): void
  (e: 'remove', conversationId: string): void
}>()

function timeLabel(item: ConversationItem): string {
  if (!item.last_active_at) return ''
  const raw = item.last_active_at.slice(0, 10)
  return raw.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$2/$3')
}

const sorted = computed(() => [...props.conversations])
</script>

<template>
  <aside class="conversation-list">
    <div class="list-head">
      <span class="list-title">我的会话</span>
      <el-button type="primary" size="small" @click="emit('create')">新建会话</el-button>
    </div>
    <el-scrollbar class="list-body">
      <div
        v-for="conversation in sorted"
        :key="conversation.conversation_id"
        class="conversation-card"
        :class="{ 'is-active': conversation.conversation_id === activeId }"
        @click="emit('select', conversation.conversation_id)"
      >
        <div class="card-main">
          <p class="card-title">{{ conversation.title ?? '未命名会话' }}</p>
          <p class="card-time">{{ timeLabel(conversation) }}</p>
        </div>
        <el-badge
          v-if="conversation.pending_suggestion_count > 0"
          :value="conversation.pending_suggestion_count"
          class="card-badge"
        >
          <el-tag size="small" type="warning" effect="plain">待处理</el-tag>
        </el-badge>
        <el-button
          class="card-delete"
          size="small"
          text
          type="danger"
          @click.stop="emit('remove', conversation.conversation_id)"
        >
          删除
        </el-button>
      </div>
      <el-empty v-if="sorted.length === 0" description="暂无会话，点击上方新建" :image-size="60" />
    </el-scrollbar>
  </aside>
</template>

<style scoped>
.conversation-list {
  width: 240px;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-right: 1px solid var(--el-border-color-light);
}

.list-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.list-title {
  font-size: 14px;
  font-weight: 700;
}

.list-body {
  flex: 1;
}

.conversation-card {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px;
  cursor: pointer;
  border-bottom: 1px solid var(--el-border-color-extra-light);
}

.conversation-card:hover {
  background: var(--el-fill-color-light);
}

.conversation-card.is-active {
  background: var(--el-color-primary-light-9);
}

.card-main {
  flex: 1;
  min-width: 0;
}

.card-title {
  margin: 0;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.card-time {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.card-delete {
  opacity: 0;
}

.conversation-card:hover .card-delete {
  opacity: 1;
}
</style>