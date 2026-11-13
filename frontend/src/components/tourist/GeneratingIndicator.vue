<script setup lang="ts">
import type { GenerateStage } from '@/stores/chat'
import { STAGE_LABELS } from '@/stores/chat'

defineProps<{ stage: GenerateStage }>()
</script>

<template>
  <div class="generating">
    <div class="skeleton-lines">
      <el-skeleton :rows="2" animated />
    </div>
    <p class="stage-text">{{ STAGE_LABELS[stage] }}</p>
  </div>
</template>

<style scoped>
.generating {
  margin: 10px 0;
  padding: 12px 16px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 12px;
  background: #fff;
  max-width: 78%;
}

.skeleton-lines {
  --el-skeleton-color: var(--el-fill-color-light);
}

.stage-text {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-color-primary);
}
</style>