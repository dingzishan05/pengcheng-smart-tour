<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useChatStore } from '@/stores/chat'

const chatStore = useChatStore()
const draft = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)

const canSend = computed(() => draft.value.trim().length > 0 && !chatStore.busy)

async function submit(): Promise<void> {
  const text = draft.value.trim()
  if (!text || chatStore.busy) return
  draft.value = ''
  await chatStore.send(text)
  await nextTick()
  inputRef.value?.focus()
}

watch(
  () => chatStore.lastFailedInput,
  (failed) => {
    if (failed) draft.value = failed
  },
)
</script>

<template>
  <div class="composer">
    <el-alert
      v-if="chatStore.lastFailedInput"
      class="error-bar"
      type="error"
      :closable="false"
      show-icon
    >
      <template #title>
        <span>服务繁忙，请重试</span>
        <el-button class="resend-btn" size="small" type="primary" plain @click="chatStore.resend()">
          重发
        </el-button>
      </template>
    </el-alert>
    <div class="input-row">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="2"
        resize="none"
        maxlength="2000"
        show-word-limit
        placeholder="描述您的徐州之旅，例如：第一次来徐州，一天时间，预算300元，喜欢历史文化"
        :disabled="chatStore.busy"
        @keydown.enter.exact.prevent="submit"
      />
      <el-button
        type="primary"
        class="send-btn"
        :disabled="!canSend"
        :loading="chatStore.busy"
        @click="submit"
      >
        发送
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.composer {
  padding: 12px 16px;
  background: #fff;
  border-top: 1px solid var(--el-border-color-light);
}

.error-bar {
  margin-bottom: 8px;
}

.resend-btn {
  margin-left: 12px;
}

.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}

.send-btn {
  height: 54px;
}
</style>