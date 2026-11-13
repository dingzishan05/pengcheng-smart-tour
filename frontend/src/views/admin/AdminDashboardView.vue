<script setup lang="ts">
import type { SourceStatusDTO } from '@/types/admin'
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchSourcesStatus } from '@/api/admin'

const sources = ref<SourceStatusDTO[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)
const lastRefresh = ref<string | null>(null)

let refreshTimer: number | undefined

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const result = await fetchSourcesStatus()
    sources.value = result.sources
    lastRefresh.value = new Date().toLocaleTimeString('zh-CN')
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '数据源状态获取失败'
  } finally {
    loading.value = false
  }
}

function availabilityType(availability: string): 'success' | 'danger' | 'info' {
  if (availability === '可用') return 'success'
  if (availability === '不可用') return 'danger'
  return 'info'
}

function timeLabel(source: SourceStatusDTO): string {
  if (!source.last_success_at) return '暂无成功采集记录'
  const raw = source.last_success_at.slice(0, 19).replace('T', ' ')
  return `最后成功采集：${raw}`
}

onMounted(() => {
  void load()
  refreshTimer = window.setInterval(() => void load(), 30_000)
})

onBeforeUnmount(() => {
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <section class="dashboard">
    <div class="dashboard-head">
      <span class="head-title">数据源状态看板</span>
      <div class="head-right">
        <span v-if="lastRefresh" class="refresh-time">更新于 {{ lastRefresh }}</span>
        <el-button size="small" :loading="loading" @click="load">手动刷新</el-button>
      </div>
    </div>
    <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon />
    <div v-loading="loading && sources.length === 0" class="source-grid">
      <el-card v-for="source in sources" :key="source.name" class="source-card" shadow="hover">
        <div class="source-head">
          <span
            class="status-dot"
            :class="{
              'dot-ok': source.availability === '可用',
              'dot-down': source.availability === '不可用',
              'dot-unknown': source.availability !== '可用' && source.availability !== '不可用',
            }"
          />
          <span class="source-name">{{ source.name }}</span>
          <el-tag size="small" :type="availabilityType(source.availability)">
            {{ source.availability }}
          </el-tag>
        </div>
        <p class="source-time">{{ timeLabel(source) }}</p>
      </el-card>
      <el-empty
        v-if="!loading && sources.length === 0 && !loadError"
        description="暂无数据源状态，请等待巡检任务首次执行"
      />
    </div>
  </section>
</template>

<style scoped>
.dashboard {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.dashboard-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.head-title {
  font-size: 16px;
  font-weight: 700;
}

.head-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refresh-time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.source-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: 14px;
  min-height: 120px;
}

.source-card :deep(.el-card__body) {
  padding: 14px 16px;
}

.source-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}

.dot-ok {
  background: var(--el-color-success);
  box-shadow: 0 0 6px var(--el-color-success-light-3);
}

.dot-down {
  background: var(--el-color-danger);
  box-shadow: 0 0 6px var(--el-color-danger-light-3);
}

.dot-unknown {
  background: var(--el-text-color-disabled);
}

.source-name {
  flex: 1;
  font-size: 15px;
  font-weight: 600;
}

.source-time {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>