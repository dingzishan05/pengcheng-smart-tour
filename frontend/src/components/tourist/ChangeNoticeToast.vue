<script setup lang="ts">
import type { ChangeNotice } from '@/stores/plan'
import { computed } from 'vue'

const props = defineProps<{ notices: ChangeNotice[] }>()

const emit = defineEmits<{ (e: 'review', notice: ChangeNotice): void; (e: 'dismiss'): void }>()

const current = computed(() => props.notices[0] ?? null)
</script>

<template>
  <transition name="toast-slide">
    <div v-if="current" class="notice-toast">
      <div class="toast-card">
        <div class="toast-head">
          <el-tag type="warning" size="small" effect="dark">行程更新提醒</el-tag>
          <span class="toast-count" v-if="notices.length > 1">还有 {{ notices.length - 1 }} 条</span>
        </div>
        <p class="toast-reason">{{ current.reason }}</p>
        <p class="toast-summary">{{ current.summary }}</p>
        <div class="toast-actions">
          <el-button size="small" type="primary" @click="emit('review', current)">查看调整建议</el-button>
          <el-button size="small" text @click="emit('dismiss')">稍后处理</el-button>
        </div>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.notice-toast {
  position: fixed;
  right: 24px;
  bottom: 24px;
  z-index: 2000;
}

.toast-card {
  width: 320px;
  padding: 14px 16px;
  border-radius: 12px;
  background: #fff;
  box-shadow: var(--el-box-shadow-light);
  border: 1px solid var(--el-border-color-light);
}

.toast-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.toast-count {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.toast-reason {
  margin: 10px 0 4px;
  font-size: 14px;
  font-weight: 600;
}

.toast-summary {
  margin: 0 0 10px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.toast-actions {
  display: flex;
  justify-content: flex-end;
  gap: 4px;
}

.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: transform 0.3s ease, opacity 0.3s ease;
}

.toast-slide-enter-from,
.toast-slide-leave-to {
  transform: translateY(20px);
  opacity: 0;
}
</style>