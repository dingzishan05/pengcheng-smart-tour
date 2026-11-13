<script setup lang="ts">
import type { ItineraryItemDTO, PoiSummary } from '@/types/api'
import PriceMetaTag from './PriceMetaTag.vue'

const props = defineProps<{ items: ItineraryItemDTO[]; poiMap: Record<string, PoiSummary> }>()

function poiName(item: ItineraryItemDTO): string {
  if (item.activity_type === '用餐') return '午餐'
  if (item.poi_id === null) return item.activity_type
  return props.poiMap[item.poi_id]?.name ?? `POI-${item.poi_id}`
}

function timeRange(item: ItineraryItemDTO): string {
  if (!item.start_at || !item.end_at) return ''
  // 后端返回带时区的 ISO 时间（如 2026-10-21T10:00:00+08:00），取本地时刻展示。
  return `${item.start_at.slice(11, 16)} - ${item.end_at.slice(11, 16)}`
}

function costLabel(item: ItineraryItemDTO): string {
  return item.estimated_cost === '0.00' ? '免费' : `¥${item.estimated_cost}`
}
</script>

<template>
  <div class="timeline">
    <el-timeline>
      <el-timeline-item
        v-for="item in items"
        :key="item.seq"
        :type="item.activity_type === '游览' ? 'primary' : item.activity_type === '用餐' ? 'success' : 'info'"
        :hollow="item.activity_type === '交通'"
        :timestamp="timeRange(item)"
        placement="top"
      >
        <div v-if="item.activity_type === '交通'" class="transport-node">
          <span class="transport-text">{{ item.transport_mode }}约{{ item.transport_minutes }}分钟</span>
        </div>
        <div v-else class="visit-node">
          <div class="visit-head">
            <span class="visit-name">{{ poiName(item) }}</span>
            <el-tag size="small" effect="plain" :type="item.activity_type === '游览' ? 'primary' : 'success'">
              {{ item.activity_type }}
            </el-tag>
          </div>
          <div class="visit-meta">
            <span class="visit-cost">{{ costLabel(item) }}</span>
            <PriceMetaTag v-if="item.activity_type === '游览'" :meta="item.cost_meta" />
          </div>
        </div>
      </el-timeline-item>
    </el-timeline>
  </div>
</template>

<style scoped>
.timeline {
  padding: 4px 8px;
}

.transport-node {
  display: flex;
  align-items: center;
}

.transport-text {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.visit-node {
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: #fff;
}

.visit-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.visit-name {
  font-size: 14px;
  font-weight: 600;
}

.visit-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.visit-cost {
  font-size: 13px;
  color: var(--el-color-danger);
  font-weight: 600;
}
</style>