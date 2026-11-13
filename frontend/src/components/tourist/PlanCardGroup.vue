<script setup lang="ts">
import type { PlanSummary } from '@/types/api'
import { computed } from 'vue'
import PlanCard from './PlanCard.vue'

const props = defineProps<{ plans: PlanSummary[]; selectedId: string | null }>()

const emit = defineEmits<{ (e: 'select', planId: string): void }>()

const converged = computed(() => {
  if (props.plans.length < 3) return false
  const costs = props.plans.map((p) => Number(p.total_cost) || 0)
  const max = Math.max(...costs)
  const min = Math.min(...costs)
  if (max <= 0) return false
  return (max - min) / max < 0.05
})
</script>

<template>
  <div class="plan-card-group">
    <el-alert
      v-if="converged"
      class="converge-bar"
      type="info"
      :closable="false"
      show-icon
      title="符合您要求的组合有限，三套方案较为接近。可尝试调整预算或兴趣偏好获得更多差异。"
    />
    <div class="cards">
      <PlanCard
        v-for="plan in plans"
        :key="plan.plan_id"
        :plan="plan"
        :selected="plan.plan_id === selectedId"
        @click="emit('select', plan.plan_id)"
      />
    </div>
  </div>
</template>

<style scoped>
.plan-card-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.converge-bar {
  padding: 4px 12px;
}

.cards {
  display: grid;
  /* 右侧方案栏只有 420px，三列并排会把“总耗时 480 分钟”挤成竖排；改为纵向卡片。 */
  grid-template-columns: 1fr;
  gap: 8px;
}
</style>