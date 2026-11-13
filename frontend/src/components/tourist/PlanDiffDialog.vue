<script setup lang="ts">
import type { ItineraryItemDTO, PoiSummary, RefreshResponse } from '@/types/api'
import { computed } from 'vue'

const props = defineProps<{
  diff: RefreshResponse | null
  visible: boolean
  poiMap: Record<string, PoiSummary>
}>()

const emit = defineEmits<{ (e: 'confirm'): void; (e: 'cancel'): void }>()

const dialogVisible = computed({
  get: () => props.visible && props.diff !== null,
  set: (value: boolean) => {
    if (!value) emit('cancel')
  },
})

function itemsOf(plan: { items: ItineraryItemDTO[] } | null): ItineraryItemDTO[] {
  return plan?.items ?? []
}

function poiName(item: ItineraryItemDTO): string {
  if (item.poi_id === null) return item.activity_type
  return props.poiMap[item.poi_id]?.name ?? `POI-${item.poi_id}`
}

const costDelta = computed(() => {
  const raw = props.diff?.diff.cost_delta
  if (raw === undefined || raw === null) return ''
  const value = Number(raw)
  if (Number.isNaN(value)) return raw
  return value > 0 ? `+¥${raw}` : `-¥${Math.abs(value).toFixed(2)}`
})

const removedSet = computed(() => new Set(props.diff?.diff.removed ?? []))
const addedSet = computed(() => new Set(props.diff?.diff.added ?? []))
</script>

<template>
  <el-dialog
    v-model="dialogVisible"
    title="方案调整对比"
    width="760px"
    :close-on-click-modal="false"
  >
    <div v-if="diff" class="diff-body">
      <div class="diff-summary">
        <el-tag type="warning" effect="plain">{{ diff.diff.reason || '已按您的调整重新规划' }}</el-tag>
        <el-tag v-if="costDelta" :type="costDelta.startsWith('-') ? 'success' : 'danger'" effect="dark">
          费用变化 {{ costDelta }}
        </el-tag>
      </div>
      <div class="diff-columns">
        <div class="diff-col">
          <p class="col-title">当前方案</p>
          <div
            v-for="item in itemsOf(diff.old_plan)"
            :key="`old-${item.seq}`"
            class="diff-item"
            :class="{ 'is-removed': removedSet.has(poiName(item)) }"
          >
            <span>{{ poiName(item) }}</span>
            <span class="item-cost">¥{{ item.estimated_cost }}</span>
          </div>
        </div>
        <div class="diff-col">
          <p class="col-title">调整后方案</p>
          <div
            v-for="item in itemsOf(diff.new_plan)"
            :key="`new-${item.seq}`"
            class="diff-item"
            :class="{ 'is-added': addedSet.has(poiName(item)) }"
          >
            <span>{{ poiName(item) }}</span>
            <span class="item-cost">¥{{ item.estimated_cost }}</span>
          </div>
        </div>
      </div>
      <p class="diff-hint">确认后新方案生效，原方案将被替换；取消则保留当前方案。</p>
    </div>
    <template #footer>
      <el-button @click="emit('cancel')">取消，保留原方案</el-button>
      <el-button type="primary" @click="emit('confirm')">确认调整</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.diff-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.diff-summary {
  display: flex;
  gap: 10px;
  align-items: center;
}

.diff-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.diff-col {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  padding: 10px;
}

.col-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.diff-item {
  display: flex;
  justify-content: space-between;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 14px;
}

.diff-item.is-removed {
  background: var(--el-color-danger-light-9);
  text-decoration: line-through;
}

.diff-item.is-added {
  background: var(--el-color-success-light-9);
  font-weight: 600;
}

.item-cost {
  color: var(--el-text-color-secondary);
}

.diff-hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>