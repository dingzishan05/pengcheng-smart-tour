<script setup lang="ts">
import type { GeoPoint } from '@/composables/useGeolocation'
import { ref } from 'vue'
import { locateMe } from '@/composables/useGeolocation'

interface Landmark {
  name: string
  point: GeoPoint
}

const LANDMARKS: Landmark[] = [
  { name: '徐州东站', point: { lng: 117.3105, lat: 34.2896 } },
  { name: '徐州站', point: { lng: 117.1996, lat: 34.2839 } },
  { name: '徐州博物馆', point: { lng: 117.1936, lat: 34.2654 } },
  { name: '户部山', point: { lng: 117.1987, lat: 34.2528 } },
  { name: '云龙湖', point: { lng: 117.1751, lat: 34.2419 } },
  { name: '徐州观音国际机场', point: { lng: 117.5548, lat: 34.0576 } },
]

const emit = defineEmits<{ (e: 'set', origin: GeoPoint): void }>()

const locating = ref(false)
const geoFailed = ref(false)
const selectedLandmark = ref<string | null>(null)
const manualLng = ref<number | null>(null)
const manualLat = ref<number | null>(null)

async function useMyLocation(): Promise<void> {
  locating.value = true
  geoFailed.value = false
  const point = await locateMe()
  locating.value = false
  if (point) {
    selectedLandmark.value = null
    emit('set', point)
  } else {
    geoFailed.value = true
  }
}

function onLandmarkChange(name: string | null): void {
  const landmark = LANDMARKS.find((l) => l.name === name)
  if (landmark) {
    manualLng.value = landmark.point.lng
    manualLat.value = landmark.point.lat
    emit('set', landmark.point)
  }
}

function applyManual(): void {
  if (manualLng.value === null || manualLat.value === null) return
  if (Math.abs(manualLng.value) > 180 || Math.abs(manualLat.value) > 90) return
  selectedLandmark.value = null
  emit('set', { lng: manualLng.value, lat: manualLat.value })
}
</script>

<template>
  <div class="start-point">
    <div class="picker-row">
      <span class="picker-label">出发地点</span>
      <el-button size="small" :loading="locating" @click="useMyLocation">使用我的定位</el-button>
      <el-select
        :model-value="selectedLandmark"
        placeholder="选择地标"
        size="small"
        style="width: 160px"
        @update:model-value="onLandmarkChange"
      >
        <el-option v-for="landmark in LANDMARKS" :key="landmark.name" :label="landmark.name" :value="landmark.name" />
      </el-select>
    </div>
    <div class="manual-row">
      <el-input-number v-model="manualLng" size="small" :controls="false" placeholder="经度" style="width: 110px" />
      <el-input-number v-model="manualLat" size="small" :controls="false" placeholder="纬度" style="width: 110px" />
      <el-button size="small" type="primary" plain @click="applyManual">地图选点</el-button>
    </div>
    <p v-if="geoFailed" class="geo-failed">定位未开启，请选择出发地点</p>
  </div>
</template>

<style scoped>
.start-point {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: #fff;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.picker-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-regular);
}

.manual-row {
  display: flex;
  gap: 6px;
}

.geo-failed {
  margin: 0;
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>