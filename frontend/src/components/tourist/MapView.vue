<script setup lang="ts">
import type { ItineraryItemDTO, PoiSummary, PlanType, TransportMode } from '@/types/api'
import type { AmapMap, AmapOverlay } from '@/types/amap'
import { PLAN_THEME_COLORS } from '@/types/amap'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { loadAmap } from '@/composables/useAmap'
import { useMapStore } from '@/stores/map'

const props = defineProps<{
  items: ItineraryItemDTO[]
  poiMap: Record<string, PoiSummary>
  selectedPlanType: PlanType | null
  origin: { lng: number; lat: number } | null
}>()

const emit = defineEmits<{ (e: 'mode-change', mode: TransportMode): void }>()

const mapStore = useMapStore()
const containerRef = ref<HTMLDivElement | null>(null)
const loadFailed = ref(false)
const mapReady = ref(false)

let map: AmapMap | null = null
let amapNs: import('@/types/amap').AmapNamespace | null = null
let overlays: AmapOverlay[] = []

function clearOverlays(): void {
  if (map && overlays.length) {
    map.remove(overlays)
  }
  overlays = []
}

function drawRoute(): void {
  if (!map || !amapNs) return
  const ns = amapNs
  clearOverlays()
  const color = PLAN_THEME_COLORS[props.selectedPlanType ?? '均衡'] ?? '#409eff'
  const path: Array<[number, number]> = []
  if (props.origin) {
    path.push([props.origin.lng, props.origin.lat])
  }
  for (const item of props.items) {
    if (item.activity_type === '交通' || item.poi_id === null) continue
    const poi = props.poiMap[item.poi_id]
    if (!poi) continue
    path.push([poi.location.lng, poi.location.lat])
  }
  if (path.length === 0) return
  const markers = path.map((position, index) =>
    new ns.Marker({
      position,
      label: {
        content: index === 0 && props.origin ? '起点' : String(index + (props.origin ? 0 : 1)),
        direction: 'top',
        offset: [0, -4],
      },
    }),
  )
  const polyline = new ns.Polyline({
    path,
    strokeColor: color,
    strokeWeight: 6,
    strokeOpacity: 0.9,
    showDir: true,
  })
  overlays = [...markers, polyline]
  map.add(overlays)
  map.setFitView(overlays)
}

onMounted(async () => {
  try {
    amapNs = await loadAmap()
    if (!containerRef.value) return
    map = new amapNs.Map(containerRef.value, {
      zoom: 12,
      center: [117.1848, 34.2618],
      viewMode: '2d',
    })
    mapReady.value = true
    drawRoute()
  } catch {
    loadFailed.value = true
  }
})

onBeforeUnmount(() => {
  clearOverlays()
  map?.destroy()
  map = null
})

watch(
  () => [props.items, props.selectedPlanType, props.origin] as const,
  () => {
    if (mapReady.value) drawRoute()
  },
  { deep: true },
)

function formatMinutes(option: { duration_minutes: number | null; unavailable?: boolean; reason?: string | null; mode: TransportMode }): string {
  if (option.unavailable) return option.reason ?? `暂无${option.mode}数据`
  if (option.duration_minutes === null || option.duration_minutes === undefined) return '暂无数据'
  return `${option.duration_minutes} 分钟`
}
</script>

<template>
  <div class="map-section">
    <div v-if="loadFailed" class="map-fallback">
      <el-icon :size="28"><i class="el-icon-map-location" /></el-icon>
      <p class="fallback-text">地图加载失败，行程信息不受影响</p>
      <p class="fallback-sub">完整行程请查看下方时间轴</p>
    </div>
    <div v-show="!loadFailed" ref="containerRef" class="map-container" />
    <div v-if="mapReady" class="mode-bar">
      <el-radio-group :model-value="mapStore.mode" size="small" @update:model-value="emit('mode-change', $event as TransportMode)">
        <el-radio-button label="公交">公交</el-radio-button>
        <el-radio-button label="步行">步行</el-radio-button>
        <el-radio-button label="驾车">驾车</el-radio-button>
      </el-radio-group>
    </div>
    <div v-if="mapStore.compareRoutes.length || mapStore.routesLoading" class="compare-bar">
      <template v-if="mapStore.routesLoading">
        <el-skeleton :rows="1" animated />
      </template>
      <template v-else>
        <div
          v-for="option in mapStore.compareRoutes"
          :key="option.mode"
          class="compare-item"
          :class="{ 'is-unavailable': option.unavailable }"
        >
          <span class="compare-mode">{{ option.mode }}</span>
          <span class="compare-duration">{{ formatMinutes(option) }}</span>
        </div>
      </template>
    </div>
    <el-alert
      v-if="mapStore.routesError"
      class="routes-error"
      type="warning"
      :title="mapStore.routesError"
      :closable="false"
      show-icon
    />
  </div>
</template>

<style scoped>
.map-section {
  position: relative;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
}

.map-container {
  width: 100%;
  height: 260px;
}

.map-fallback {
  height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: var(--el-fill-color-light);
}

.fallback-text {
  margin: 0;
  font-size: 14px;
  color: var(--el-text-color-regular);
}

.fallback-sub {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.mode-bar {
  display: flex;
  justify-content: center;
  padding: 8px 0;
  border-top: 1px solid var(--el-border-color-lighter);
}

.compare-bar {
  display: flex;
  gap: 10px;
  padding: 8px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.compare-item {
  flex: 1;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--el-fill-color-light);
  font-size: 13px;
}

.compare-item.is-unavailable {
  color: var(--el-text-color-secondary);
}

.compare-mode {
  font-weight: 600;
}

.routes-error {
  border-top: 1px solid var(--el-border-color-lighter);
  border-radius: 0;
}
</style>