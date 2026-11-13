<script setup lang="ts">
import type { AuditLogDTO } from '@/types/admin'
import { computed, onMounted, reactive, ref } from 'vue'
import { listAudits } from '@/api/admin'

const ENTITY_TYPES = ['user', 'poi', 'price_record', 'knowledge_document', 'itinerary', 'plan', 'conversation', 'data_source']

interface FilterForm {
  operatorId: number | null
  entityType: string
  entityId: number | null
  range: [string, string] | null
}

const filterForm = reactive<FilterForm>({
  operatorId: null,
  entityType: '',
  entityId: null,
  range: null,
})

const items = ref<AuditLogDTO[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)
const loadError = ref<string | null>(null)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const result = await listAudits({
      operator_id: filterForm.operatorId ?? undefined,
      entity_type: filterForm.entityType || undefined,
      entity_id: filterForm.entityId ?? undefined,
      since: filterForm.range?.[0] ?? undefined,
      until: filterForm.range?.[1] ?? undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    items.value = result.items
    total.value = result.total
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '审计日志获取失败'
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

function applyFilter(): void {
  page.value = 1
  void load()
}

function resetFilter(): void {
  filterForm.operatorId = null
  filterForm.entityType = ''
  filterForm.entityId = null
  filterForm.range = null
  page.value = 1
  void load()
}

function onPageChange(next: number): void {
  page.value = next
  void load()
}

interface FieldRow {
  key: string
  before: string
  after: string
  changed: boolean
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}


const rowFieldRows = computed(() => {
  const map: Record<string, FieldRow[]> = {}
  for (const row of items.value) {
    const before = row.before_value ?? {}
    const after = row.after_value ?? {}
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    map[row.audit_id] = keys.map((key) => {
      const b = key in before ? before[key] : null
      const a = key in after ? after[key] : null
      return {
        key,
        before: formatValue(b),
        after: formatValue(a),
        changed: JSON.stringify(b) !== JSON.stringify(a),
      }
    })
  }
  return map
})

function timeLabel(value: string | null): string {
  return value ? value.slice(0, 19).replace('T', ' ') : '—'
}
</script>

<template>
  <section class="audits">
    <div class="audits-head">
      <span class="head-title">审计日志</span>
      <span class="head-sub">谁改的 / 何时 / 改了什么（before → after 对照）</span>
    </div>

    <el-card shadow="never" class="filter-card">
      <div class="filter-row">
        <el-input-number
          v-model="filterForm.operatorId"
          :min="1"
          :controls="false"
          placeholder="操作者 ID"
          style="width: 130px"
        />
        <el-select
          v-model="filterForm.entityType"
          placeholder="全部对象类型"
          clearable
          filterable
          allow-create
          style="width: 180px"
        >
          <el-option v-for="t in ENTITY_TYPES" :key="t" :label="t" :value="t" />
        </el-select>
        <el-input-number
          v-model="filterForm.entityId"
          :min="1"
          :controls="false"
          placeholder="对象 ID"
          style="width: 110px"
        />
        <el-date-picker
          v-model="filterForm.range"
          type="datetimerange"
          value-format="YYYY-MM-DDTHH:mm:ss"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          style="width: 360px"
        />
        <el-button type="primary" @click="applyFilter">查询</el-button>
        <el-button @click="resetFilter">重置</el-button>
      </div>
    </el-card>

    <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon />

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      row-key="audit_id"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <div class="expand-body">
            <p class="expand-title">变更对照（{{ row.action }} · {{ row.entity_type }}#{{ row.entity_id }}）</p>
            <el-table :data="rowFieldRows[row.audit_id] ?? []" size="small" border>
              <el-table-column prop="key" label="字段" width="160" />
              <el-table-column label="变更前" min-width="220">
                <template #default="{ row: field }">
                  <pre class="value-pre" :class="{ 'value-old': field.changed }">{{ field.before }}</pre>
                </template>
              </el-table-column>
              <el-table-column label="变更后" min-width="220">
                <template #default="{ row: field }">
                  <pre class="value-pre" :class="{ 'value-new': field.changed }">{{ field.after }}</pre>
                </template>
              </el-table-column>
              <el-table-column label="变化" width="80" align="center">
                <template #default="{ row: field }">
                  <el-tag v-if="field.changed" size="small" type="warning">变更</el-tag>
                  <el-tag v-else size="small" type="info" effect="plain">—</el-tag>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </template>
      </el-table-column>
      <el-table-column prop="action" label="操作" min-width="170" show-overflow-tooltip />
      <el-table-column prop="entity_type" label="对象类型" width="150" />
      <el-table-column prop="entity_id" label="对象 ID" width="90" />
      <el-table-column prop="operator_id" label="操作者 ID" width="100" />
      <el-table-column label="操作时间" width="170">
        <template #default="{ row }">{{ timeLabel(row.operated_at) }}</template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无审计记录" />
      </template>
    </el-table>

    <div class="pager">
      <el-pagination
        layout="total, prev, pager, next, jumper"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPageChange"
      />
    </div>
  </section>
</template>

<style scoped>
.audits {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.audits-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.head-title {
  font-size: 16px;
  font-weight: 700;
}

.head-sub {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.filter-card :deep(.el-card__body) {
  padding: 12px 16px;
}

.filter-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.expand-body {
  padding: 6px 12px 10px;
}

.expand-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.value-pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: inherit;
}

.value-old {
  color: var(--el-color-danger);
}

.value-new {
  color: var(--el-color-success);
}

.pager {
  display: flex;
  justify-content: flex-end;
}
</style>