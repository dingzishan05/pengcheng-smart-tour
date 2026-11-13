<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ questions: string[]; round: number }>()

const emit = defineEmits<{ (e: 'answer', text: string): void }>()

const draft = ref('')

function submit(): void {
  const text = draft.value.trim()
  if (!text) return
  emit('answer', text)
  draft.value = ''
}
</script>

<template>
  <div class="clarify-card">
    <div class="clarify-header">
      <el-tag size="small" type="warning" effect="plain">第 {{ props.round }} 轮追问</el-tag>
      <span class="clarify-hint">回答后即可继续生成方案</span>
    </div>
    <p v-for="(question, index) in props.questions" :key="index" class="clarify-question">
      {{ question }}
    </p>
    <div class="clarify-input">
      <el-input
        v-model="draft"
        placeholder="请输入您的回答"
        @keyup.enter="submit"
      />
      <el-button type="primary" @click="submit">提交回答</el-button>
    </div>
  </div>
</template>

<style scoped>
.clarify-card {
  margin: 10px 0;
  padding: 12px 16px;
  border: 1px solid var(--el-color-warning-light-5);
  border-radius: 12px;
  background: var(--el-color-warning-light-9);
}

.clarify-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.clarify-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.clarify-question {
  margin: 6px 0 10px;
  font-size: 14px;
  line-height: 1.6;
  color: var(--el-text-color-primary);
}

.clarify-input {
  display: flex;
  gap: 8px;
}
</style>