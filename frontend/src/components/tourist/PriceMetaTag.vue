<script setup lang="ts">
import type { CostMeta } from '@/types/api'
import { computed } from 'vue'

const props = defineProps<{ meta: CostMeta }>()

const collectedLabel = computed(() => {
  if (!props.meta.collected_at) return '采集时间未知'
  const raw = props.meta.collected_at.slice(0, 10)
  return `${raw.slice(5, 7).replace(/^0/, '')}-${raw.slice(8, 10).replace(/^0/, '')}采集`
})

const normalText = computed(() => `${props.meta.source_channel || '未知来源'} · ${collectedLabel.value}`)
</script>

<template>
  <el-tooltip
    v-if="meta.is_reference"
    content="价格可能已变化，请以现场为准"
    placement="top"
  >
    <el-tag size="small" type="warning" effect="plain">
      {{ normalText }} · 可能已变化
    </el-tag>
  </el-tooltip>
  <el-tag v-else size="small" type="info" effect="plain">{{ normalText }}</el-tag>
</template>