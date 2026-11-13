<script setup lang="ts">
import { computed, ref } from 'vue'
import ItineraryTimeline from '@/components/tourist/ItineraryTimeline.vue'
import MapView from '@/components/tourist/MapView.vue'
import PlanCardGroup from '@/components/tourist/PlanCardGroup.vue'
import StartPointPicker from '@/components/tourist/StartPointPicker.vue'
import type { GeoPoint } from '@/composables/useGeolocation'
import { usePlanStore } from '@/stores/plan'

defineProps<{ origin: GeoPoint | null }>()

const emit = defineEmits<{
  (e: 'set-origin', origin: GeoPoint): void
  (e: 'mode-change', mode: import('@/types/api').TransportMode): void
}>()

const planStore = usePlanStore()
const budgetInput = ref<number | null>(null)

const dataTimeLabel = computed(() => {
  if (!planStore.dataTime) return null
  const raw = planStore.dataTime.slice(0, 10)
  return `路线耗时基于 ${raw.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$1年$2月$3日')} 数据`
})

function selectPlan(planId: string): void {
  void planStore.selectPlan(planId)
}

function applyBudget(): void {
  if (budgetInput.value === null || !planStore.selectedPlanId) return
  void planStore.refresh(planStore.selectedPlanId, { budget: String(budgetInput.value) })
}

function adoptSuggestedBudget(): void {
  const suggestion = planStore.infeasible?.minBudgetSuggestion
  if (!suggestion || !planStore.selectedPlanId) return
  budgetInput.value = Number(suggestion)
  void planStore.refresh(planStore.selectedPlanId, { budget: suggestion })
}

const detail = computed(() => planStore.selectedDetail)

</script>

<template>
  <div class="plan-panel-content">
    <div v-if="planStore.plans.length === 0" class="panel-empty">
      三套方案将在此展示，先在左侧聊天中描述您的需求吧
    </div>
    <template v-else>
      <PlanCardGroup
        :plans="planStore.plans"
        :selected-id="planStore.selectedPlanId"
        @select="selectPlan"
      />
      <div v-if="dataTimeLabel" class="data-time-bar">⚠ {{ dataTimeLabel }}</div>
      <div v-if="planStore.refreshError" class="refresh-error-bar">{{ planStore.refreshError }}</div>
      <div v-if="planStore.infeasible" class="infeasible-card">
        <p class="infeasible-title">当前约束下暂无可行行程</p>
        <p v-for="(reason, index) in planStore.infeasible.reasons" :key="index" class="infeasible-reason">
          {{ reason }}
        </p>
        <el-button
          v-if="planStore.infeasible.minBudgetSuggestion"
          class="adopt-btn"
          type="primary"
          size="small"
          @click="adoptSuggestedBudget"
        >
          建议增加预算至 {{ planStore.infeasible.minBudgetSuggestion }} 元，一键采纳
        </el-button>
      </div>
      <StartPointPicker @set="emit('set-origin', $event)" />
      <div v-if="planStore.detailLoading" class="detail-loading" v-loading="true" element-loading-text="正在加载方案详情…">
        <div class="loading-placeholder" />
      </div>
      <template v-else-if="detail">
        <ItineraryTimeline :items="detail.items" :poi-map="planStore.poiMap" />
        <div v-if="detail.solver_note" class="solver-note">方案为快速生成，仅供参考</div>
        <div class="budget-adjust">
          <el-input-number
            v-model="budgetInput"
            :min="0"
            :controls="false"
            placeholder="调整预算（元）"
            style="width: 100%"
          />
          <el-button type="primary" plain @click="applyBudget">重新规划</el-button>
        </div>
        <MapView
          :items="detail.items"
          :poi-map="planStore.poiMap"
          :selected-plan-type="detail.plan_type"
          :origin="origin"
          @mode-change="emit('mode-change', $event)"
        />
      </template>
    </template>
  </div>
</template>

<style scoped>
.plan-panel-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.panel-empty {
  padding: 40px 0;
  text-align: center;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.data-time-bar {
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning-dark-2);
  font-size: 12px;
}

.refresh-error-bar {
  padding: 6px 12px;
  border-radius: 8px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  font-size: 12px;
}

.infeasible-card {
  padding: 12px;
  border: 1px solid var(--el-color-danger-light-5);
  border-radius: 10px;
  background: var(--el-color-danger-light-9);
}

.infeasible-title {
  margin: 0 0 6px;
  font-weight: 700;
  color: var(--el-color-danger);
}

.infeasible-reason {
  margin: 2px 0;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.adopt-btn {
  margin-top: 8px;
}

.detail-loading {
  min-height: 160px;
}

.loading-placeholder {
  height: 160px;
}

.solver-note {
  padding: 4px 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
  border-radius: 6px;
}

.budget-adjust {
  display: flex;
  gap: 8px;
}
</style>