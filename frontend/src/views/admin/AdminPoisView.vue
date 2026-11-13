<script setup lang="ts">
import type { AdminPoiRow, PoiStatusResult } from '@/types/admin'
import type { FormInstance, FormRules } from 'element-plus'
import { computed, h, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { createPoi, fetchPoisForAdmin, setPoiStatus, updatePoi } from '@/api/admin'
import { getPoiDetail } from '@/api/client'

const POI_CATEGORIES = ['景点', '博物馆', '餐饮', '商圈', '交通枢纽']
const LON_MIN = 116.9
const LON_MAX = 117.6
const LAT_MIN = 34.1
const LAT_MAX = 34.4

const rows = ref<AdminPoiRow[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)

const categoryFilter = ref('')
const statusFilter = ref('')
const keyword = ref('')

const filteredRows = computed<AdminPoiRow[]>(() =>
  rows.value.filter((row) => {
    if (categoryFilter.value && row.category !== categoryFilter.value) return false
    if (statusFilter.value && row.status !== statusFilter.value) return false
    const word = keyword.value.trim()
    if (word && !row.name.includes(word)) return false
    return true
  }),
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const result = await fetchPoisForAdmin()
    rows.value = result.items
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : 'POI 列表获取失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

function coordLabel(row: AdminPoiRow): string {
  return `${row.location.lng.toFixed(4)}, ${row.location.lat.toFixed(4)}`
}

function durationLabel(minutes: number): string {
  if (minutes < 60) return `${minutes} 分钟`
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60 ? `${minutes % 60} 分` : ''}`.trim()
}

const statusBusy = ref<string | null>(null)

async function toggleStatus(row: AdminPoiRow): Promise<void> {
  const target = row.status === '有效' ? '失效' : '有效'
  const action = target === '失效' ? '设为失效' : '恢复有效'
  const tip =
    target === '失效'
      ? '失效后立即从游客侧候选移除，并为引用该 POI 的行程生成重规划建议。'
      : '恢复后立即重新进入游客侧候选。'
  try {
    await ElMessageBox.confirm(`确认将「${row.name}」${action}？${tip}`, '状态变更确认', {
      confirmButtonText: action,
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  statusBusy.value = row.poi_id
  try {
    const result = await setPoiStatus(row.poi_id, target)
    row.status = result.status
    showStatusResult(row.name, result)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '状态变更失败')
  } finally {
    statusBusy.value = null
  }
}

function showStatusResult(name: string, result: PoiStatusResult): void {
  const children = [
    h('p', { style: 'margin:0 0 8px' }, [
      h('strong', null, `「${name}」已${result.status === '有效' ? '恢复有效' : '设为失效'}，`),
      h('span', { style: 'color:var(--el-color-success);font-weight:600' }, '立即生效'),
      h('span', null, '（游客侧候选与缓存已同步刷新）'),
    ]),
    h('p', { style: 'margin:0 0 8px;color:var(--el-text-color-secondary)' }, `受影响行程：${result.affected_itinerary_ids.length} 个`),
  ]
  if (result.status === '失效') {
    const created = result.suggestions.filter((item) => item.created)
    children.push(
      h('p', { style: 'margin:0 0 8px;color:var(--el-text-color-secondary)' }, `已生成重规划建议：${created.length} 条`),
    )
    if (created.length > 0) {
      children.push(
        h(
          'ul',
          { style: 'margin:0;padding-left:18px;color:var(--el-text-color-secondary);font-size:13px' },
          created.slice(0, 5).map((item) => h('li', null, `行程 ${item.plan_id}：${item.reason ?? '建议重新规划'}`)),
        ),
      )
    }
  }
  ElMessageBox.alert(h('div', null, children), '状态变更结果', { confirmButtonText: '知道了' })
}

interface PoiForm {
  name: string
  category: string
  tags: string[]
  longitude: number | null
  latitude: number | null
  city: string
  visit_minutes: number | null
  indoor: boolean
  description: string
  source_channel: string
  collected_at: string
}

const drawerVisible = ref(false)
const drawerMode = ref<'create' | 'edit'>('create')
const editingId = ref<string | null>(null)
const drawerLoading = ref(false)
const submitting = ref(false)
const formRef = ref<FormInstance>()

const form = reactive<PoiForm>({
  name: '',
  category: '',
  tags: [],
  longitude: null,
  latitude: null,
  city: '徐州市',
  visit_minutes: null,
  indoor: false,
  description: '',
  source_channel: '',
  collected_at: '',
})

const rules: FormRules<PoiForm> = {
  name: [
    { required: true, message: 'POI 名称必填', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) =>
        callback(value && value.trim() ? undefined : new Error('POI 名称必填')),
      trigger: 'blur',
    },
  ],
  category: [{ required: true, message: '类别必须是景点/博物馆/餐饮/商圈/交通枢纽 之一', trigger: 'change' }],
  tags: [{ required: true, type: 'array' as const, min: 1, message: '主题标签至少 1 个', trigger: 'change' }],
  longitude: [
    { required: true, message: '经度必填', trigger: 'blur' },
    {
      type: 'number' as const,
      min: LON_MIN,
      max: LON_MAX,
      message: `经度需在 ${LON_MIN}—${LON_MAX}（徐州市区范围）`,
      trigger: 'blur',
    },
  ],
  latitude: [
    { required: true, message: '纬度必填', trigger: 'blur' },
    {
      type: 'number' as const,
      min: LAT_MIN,
      max: LAT_MAX,
      message: `纬度需在 ${LAT_MIN}—${LAT_MAX}（徐州市区范围）`,
      trigger: 'blur',
    },
  ],
  city: [{ required: true, message: '所在城市必填', trigger: 'blur' }],
  visit_minutes: [
    { required: true, message: '常规游览时长必填', trigger: 'blur' },
    {
      validator: (_rule, value: number | null, callback) =>
        callback(value !== null && Number.isInteger(value) && value > 0
          ? undefined
          : new Error('常规游览时长必须为大于 0 的整数分钟')),
      trigger: 'blur',
    },
  ],
  source_channel: [{ required: true, message: '来源渠道必填（如 运营录入/高德）', trigger: 'blur' }],
  collected_at: [{ required: true, message: '采集日期必填', trigger: 'change' }],
}

function resetForm(): void {
  form.name = ''
  form.category = ''
  form.tags = []
  form.longitude = null
  form.latitude = null
  form.city = '徐州市'
  form.visit_minutes = null
  form.indoor = false
  form.description = ''
  form.source_channel = ''
  form.collected_at = ''
}

function openCreate(): void {
  drawerMode.value = 'create'
  editingId.value = null
  resetForm()
  drawerVisible.value = true
}

async function openEdit(row: AdminPoiRow): Promise<void> {
  drawerMode.value = 'edit'
  editingId.value = row.poi_id
  resetForm()
  drawerVisible.value = true
  drawerLoading.value = true
  try {
    const detail = await getPoiDetail(row.poi_id)
    form.name = detail.name
    form.category = detail.category
    form.tags = [...detail.tags]
    form.longitude = detail.location.lng
    form.latitude = detail.location.lat
    form.city = detail.city ?? '徐州市'
    form.visit_minutes = detail.visit_minutes
    form.indoor = detail.indoor
    form.description = detail.description ?? ''
    form.source_channel = detail.source_channel ?? ''
    form.collected_at = detail.collected_at ? detail.collected_at.slice(0, 10) : ''
  } catch (error) {
    drawerVisible.value = false
    ElMessage.error(error instanceof Error ? error.message : 'POI 详情获取失败')
  } finally {
    drawerLoading.value = false
  }
}

async function submit(): Promise<void> {
  const inst = formRef.value
  if (!inst) return
  const valid = await inst.validate().catch(() => false)
  if (!valid) return
  if (form.longitude === null || form.latitude === null || form.visit_minutes === null) return
  submitting.value = true
  try {
    const payload = {
      name: form.name.trim(),
      category: form.category,
      tags: form.tags,
      longitude: form.longitude,
      latitude: form.latitude,
      city: form.city,
      visit_minutes: form.visit_minutes,
      indoor: form.indoor,
      description: form.description.trim() || null,
      source_channel: form.source_channel,
      collected_at: form.collected_at,
    }
    if (drawerMode.value === 'create') {
      await createPoi(payload)
      ElMessage.success('POI 创建成功，已同步至游客侧候选')
    } else {
      await updatePoi({ poi_id: Number(editingId.value), ...payload })
      ElMessage.success('POI 更新成功，已同步至游客侧候选')
    }
    drawerVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <section class="pois">
    <div class="pois-head">
      <span class="head-title">POI 管理</span>
      <div class="head-filters">
        <el-select v-model="categoryFilter" placeholder="全部类别" clearable style="width: 130px">
          <el-option v-for="cat in POI_CATEGORIES" :key="cat" :label="cat" :value="cat" />
        </el-select>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 110px">
          <el-option label="有效" value="有效" />
          <el-option label="失效" value="失效" />
        </el-select>
        <el-input v-model="keyword" placeholder="按名称搜索" clearable style="width: 180px" />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button type="primary" @click="openCreate">新建 POI</el-button>
      </div>
    </div>
    <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon />
    <el-table v-loading="loading" :data="filteredRows" stripe>
      <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
      <el-table-column prop="category" label="类别" width="90">
        <template #default="{ row }">
          <el-tag size="small">{{ row.category }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="主题标签" min-width="160" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.tags.join(' / ') }}
        </template>
      </el-table-column>
      <el-table-column label="坐标" width="130">
        <template #default="{ row }">
          {{ coordLabel(row) }}
        </template>
      </el-table-column>
      <el-table-column label="游览时长" width="110">
        <template #default="{ row }">
          {{ durationLabel(row.visit_minutes) }}
        </template>
      </el-table-column>
      <el-table-column label="门票" width="90" align="right">
        <template #default="{ row }">
          {{ row.ticket_price ?? '—' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="row.status === '有效' ? 'success' : 'danger'">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button
            link
            :type="row.status === '有效' ? 'danger' : 'success'"
            :loading="statusBusy === row.poi_id"
            @click="toggleStatus(row)"
          >
            {{ row.status === '有效' ? '设为失效' : '恢复有效' }}
          </el-button>
        </template>
      </el-table-column>
      <template #empty>
        <el-empty description="暂无 POI 数据" />
      </template>
    </el-table>
    <p class="table-meta">共 {{ filteredRows.length }} 条（{{ rows.length }} 条总数）</p>

    <el-drawer
      v-model="drawerVisible"
      :title="drawerMode === 'create' ? '新建 POI' : `编辑 POI（ID ${editingId}）`"
      size="560px"
      destroy-on-close
    >
      <div v-loading="drawerLoading" class="drawer-body">
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
          <el-form-item label="名称" prop="name">
            <el-input v-model="form.name" placeholder="如 徐州博物馆" maxlength="40" />
          </el-form-item>
          <el-form-item label="类别" prop="category">
            <el-select v-model="form.category" placeholder="选择类别" style="width: 100%">
              <el-option v-for="cat in POI_CATEGORIES" :key="cat" :label="cat" :value="cat" />
            </el-select>
          </el-form-item>
          <el-form-item label="主题标签（至少 1 个，回车添加）" prop="tags">
            <el-select
              v-model="form.tags"
              multiple
              filterable
              allow-create
              default-first-option
              placeholder="如 历史 / 免费 / 亲子"
              style="width: 100%"
            />
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="经度" prop="longitude">
              <el-input-number
                v-model="form.longitude"
                :min="LON_MIN"
                :max="LON_MAX"
                :step="0.0001"
                :precision="6"
                :controls="false"
                placeholder="116.9—117.6"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="纬度" prop="latitude">
              <el-input-number
                v-model="form.latitude"
                :min="LAT_MIN"
                :max="LAT_MAX"
                :step="0.0001"
                :precision="6"
                :controls="false"
                placeholder="34.1—34.4"
                style="width: 100%"
              />
            </el-form-item>
          </div>
          <div class="form-grid">
            <el-form-item label="所在城市" prop="city">
              <el-input v-model="form.city" />
            </el-form-item>
            <el-form-item label="常规游览时长（分钟）" prop="visit_minutes">
              <el-input-number
                v-model="form.visit_minutes"
                :min="1"
                :step="15"
                :controls="false"
                placeholder="> 0"
                style="width: 100%"
              />
            </el-form-item>
          </div>
          <el-form-item label="室内场所">
            <el-switch v-model="form.indoor" />
          </el-form-item>
          <el-form-item label="描述" prop="description">
            <el-input v-model="form.description" type="textarea" :rows="3" placeholder="选填" />
          </el-form-item>
          <div class="form-grid">
            <el-form-item label="来源渠道" prop="source_channel">
              <el-input v-model="form.source_channel" placeholder="如 运营录入" />
            </el-form-item>
            <el-form-item label="采集日期" prop="collected_at">
              <el-date-picker
                v-model="form.collected_at"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                style="width: 100%"
              />
            </el-form-item>
          </div>
          <div class="drawer-actions">
            <el-button @click="drawerVisible = false">取消</el-button>
            <el-button type="primary" :loading="submitting" @click="submit">
              {{ drawerMode === 'create' ? '创建' : '保存' }}
            </el-button>
          </div>
        </el-form>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.pois {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.pois-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.head-title {
  font-size: 16px;
  font-weight: 700;
}

.head-filters {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.table-meta {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.drawer-body {
  min-height: 200px;
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.drawer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 8px;
}
</style>