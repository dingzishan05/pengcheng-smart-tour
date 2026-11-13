<script setup lang="ts">

import { computed } from 'vue'
import type { ChatMessage } from '@/stores/chat'

const props = defineProps<{ message: ChatMessage }>()

const emit = defineEmits<{ (e: 'view-plans'): void }>()

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function renderInline(text: string): string {
  return escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
}

// 大模型解释内容是 Markdown（### 标题、**加粗**、- 列表），
// 这里做最小渲染；先转义再替换，避免 XSS。
function renderMarkdown(content: string): string {
  const lines = content.split('\n')
  const html: string[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      html.push('<div class="md-gap"></div>')
      continue
    }
    const heading = /^#{1,6}\s+(.*)$/.exec(trimmed)
    if (heading) {
      html.push(`<div class="md-heading">${renderInline(heading[1] ?? '')}</div>`)
      continue
    }
    const bullet = /^[-*]\s+(.*)$/.exec(trimmed)
    if (bullet) {
      html.push(`<div class="md-item">• ${renderInline(bullet[1] ?? '')}</div>`)
      continue
    }
    if (/^\d+[.、]\s*/.test(trimmed)) {
      html.push(`<div class="md-item">${renderInline(trimmed)}</div>`)
      continue
    }
    html.push(`<div class="md-line">${renderInline(line)}</div>`)
  }
  return html.join('')
}

const renderedContent = computed(() => {
  if (props.message.role !== 'system') return ''
  return renderMarkdown(props.message.content)
})
</script>

<template>
  <div class="bubble-row" :class="message.role === 'user' ? 'is-user' : 'is-system'">
    <div class="bubble" :class="message.role === 'user' ? 'bubble-user' : 'bubble-system'">
      <p v-if="message.role === 'user'" class="bubble-content">{{ message.content }}</p>
      <div v-else class="bubble-content markdown-body" v-html="renderedContent" />
      <div v-if="message.role === 'system' && message.sources && message.sources.length" class="source-footnotes">
        <p class="footnote-title">参考来源：</p>
        <p v-for="(source, index) in message.sources" :key="index" class="footnote-item">
          [{{ index + 1 }}] {{ source.doc_title }}（{{ source.source }}）
        </p>
      </div>
      <el-button
        v-if="message.role === 'system' && message.plan_refs && message.plan_refs.length"
        class="view-plans-btn"
        size="small"
        type="primary"
        plain
        @click="emit('view-plans')"
      >
        查看方案
      </el-button>
    </div>
  </div>
</template>


<style scoped>
.bubble-row {
  display: flex;
  margin: 10px 0;
}

.bubble-row.is-user {
  justify-content: flex-end;
}

.bubble-row.is-system {
  justify-content: flex-start;
}

.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}

.bubble-user {
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  color: var(--el-text-color-primary);
}

.bubble-system {
  background: #fff;
  border: 1px solid var(--el-border-color-light);
  color: var(--el-text-color-primary);
}

.bubble-content {
  margin: 0;
  white-space: pre-wrap;
}

.markdown-body :deep(.md-heading) {
  margin: 4px 0 2px;
  font-weight: 700;
  font-size: 14px;
  color: var(--el-text-color-primary);
}

.markdown-body :deep(.md-gap) {
  height: 6px;
}

.markdown-body :deep(.md-item) {
  padding-left: 4px;
}

.markdown-body :deep(strong) {
  color: var(--el-text-color-primary);
}

.source-footnotes {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--el-border-color);
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.footnote-title {
  margin: 0 0 2px;
}

.footnote-item {
  margin: 0;
}

.view-plans-btn {
  margin-top: 8px;
}
</style>
