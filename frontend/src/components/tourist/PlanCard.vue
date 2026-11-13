<script setup lang="ts">
import type { PlanSummary } from '@/types/api'
import { computed } from 'vue'

const props = defineProps<{ plan: PlanSummary; selected: boolean }>()

const typeTag: Record<string, 'success' | 'warning' | 'primary'> = {
  省钱: 'success',
  省时: 'warning',
  均衡: 'primary',
}

const hoursLabel = computed(() => {
  const hours = Math.floor(props.plan.total_minutes / 60)
  const minutes = props.plan.total_minutes % 60
  return minutes > 0 ? `${hours}小时${minutes}分` : `${hours}小时`
})
</script>

<template>
  <div class="plan-card" :class="{ 'is-selected': selected }">
    <div class="card-head">
      <el-tag :type="typeTag[plan.plan_type] ?? 'info'" effect="dark">{{ plan.plan_type }}</el-tag>
      <el-tag v-if="plan.status === '已确认'" type="success" size="small">已确认</el-tag>
      <el-tag v-else-if="plan.status === '挂起建议'" type="warning" size="small">挂起建议</el-tag>
    </div>
    <div class="card-stats">
      <div class="stat">
        <span class="stat-label">总费用</span>
        <span class="stat-value">¥{{ plan.total_cost }}</span>
      </div>
      <div class="stat">
        <span class="stat-label">总耗时</span>
        <span class="stat-value">{{ hoursLabel }}</span>
      </div>
      <div class="stat">
        <span class="stat-label">交通耗时</span>
        <span class="stat-value">{{ plan.transport_minutes }} 分钟</span>
      </div>
    </div>
    <ul class="highlights">
      <li v-for="(highlight, index) in plan.highlights" :key="index">{{ highlight }}</li>
    </ul>
    <p class="suited-for">{{ plan.suited_for }}</p>
  </div>
</template>

<style scoped>
.plan-card {
  padding: 12px;
  border: 2px solid var(--el-border-color-light);
  border-radius: 10px;
  background: #fff;
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.plan-card:hover {
  border-color: var(--el-color-primary-light-5);
}

.plan-card.is-selected {
  border-color: var(--el-color-primary);
  box-shadow: 0 2px 12px rgba(64, 158, 255, 0.15);
}

.card-head {
  display: flex;
  gap: 6px;
  align-items: center;
}

.card-stats {
  display: flex;
  gap: 12px;
  margin-top: 10px;
}

.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.stat-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.stat-value {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.highlights {
  margin: 10px 0 0;
  padding-left: 18px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.highlights li {
  margin: 2px 0;
}

.suited-for {
  margin: 8px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>