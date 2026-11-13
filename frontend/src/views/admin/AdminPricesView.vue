<script setup lang="ts">
import type { AdminPoiRow, PriceRecordDTO, PriceReviewResult } from '@/types/admin'
import type { FormInstance, FormRules } from 'element-plus'
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { fetchPoisForAdmin, fetchPriceHistory, reviewPrice, submitPrice } from '@/api/admin'

const PRICE_TYPES = ['门票', '交通', '餐饮']

const pois = ref<AdminPoiRow[]>([])
const poisLoading = ref(false)
const selectedPoiId = ref('')

const records = ref<PriceRecordDTO[]>([])
const currentRecord = ref<PriceRecordDTO | null>(null)
const historyLoading = ref(false)
const historyError = ref<string | null>(null)

const pendingRecords = computed<PriceRecordDTO[]>(() =>
  records.value.filter((record) => record.review_status === '待审核'),
)

const lastReviewResult = ref<PriceReviewResult | null>(null)
const reviewingId = ref<string | null>(null)

async function loadPois(): Promise<void> {
  poisLoading.value = true
  try {
    const result = await fetchPoisForAdmin()
    pois.value = result.items
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : 'POI 列表获取失败')
  } finally {
    poisLoading.value = false
  }
}

async function loadHistory(): Promise<void> {
  if (!selectedPoiId.value) return
  historyLoading.value = true
  historyError.value = null
  try {
    const result = await fetchPriceHistory(selectedPoiId.value)
    records.value = result.records
    currentRecord.value = result.current
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : '价格历史获取失败'
    records.value = []
    currentRecord.value = null
  } finally {
    historyLoading.value = false
  }
}

onMounted(() => void loadPois())

watch(selectedPoiId, () => {
  lastReviewResult.value = null
  void loadHistory()
})

interface SubmitForm {
  price_type: string
  amount: number | null
  source_channel: string
  collected_at: string
  valid_until: string
}

const submitRef = ref<FormInstance>()
const submitting = ref(false)
const submitForm = reactive<SubmitForm>({
  price_type: '',
  amount: null,
  source_channel: '',
  collected_at: '',
  valid_until: '',
})

const submitRules: FormRules<SubmitForm> = {
  price_type: [{ required: true, message: '价格类型必须是门票/交通/餐饮 之一', trigger: 'change' }],
  amount: [
    { required: true, message: '金额必填（免费请填 0）', trigger: 'blur' },
    {
      validator: (_rule, value: number | null, callback) =>
        callback(value !== null && value >= 0 ? undefined : new Error('金额不能为负，免费填 0')),
      trigger: 'blur',
    },
  ],
  source_channel: [{ required: true, message: '来源渠道必填（如 运营录入/美团/高德）', trigger: 'blur' }],
  collected_at: [{ required: true, message: '采集日期必填', trigger: 'change' }],
  valid_until: [{ required: true, message: '有效期至必填', trigger: 'change' }],
}

function resetSubmitForm(): void {
  submitForm.price_type = ''
  submitForm.amount = null
  submitForm.source_channel = ''
  submitForm.collected_at = ''
  submitForm.valid_until = ''
}

async function submit(): Promise<void> {
  const inst = submitRef.value
  if (!inst || !selectedPoiId.value) return
  const valid = await inst.validate().catch(() => false)
  if (!valid || submitForm.amount === null) return
  submitting.value = true
  try {
    await submitPrice({
      poi_id: Number(selectedPoiId.value),
      price_type: submitForm.price_type,
      amount: submitForm.amount,
      source_channel: submitForm.source_channel,
      collected_at: submitForm.collected_at,
      valid_until: submitForm.valid_until,
    })
    ElMessage.success('价格已提交，进入待审核队列（游客侧暂不可见）')
    resetSubmitForm()
    await loadHistory()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '提交失败')
  } finally {
    submitting.value = false
  }
}

async function approve(record: PriceRecordDTO): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认通过该价格记录（¥${record.amount} / ${record.price_type}）？通过后立即生效并刷新缓存。`,
      '审核确认',
      { confirmButtonText: '通过', cancelButtonText: '取消', type: 'info' },
    )
  } catch {
    return
  }
  reviewingId.value = record.record_id
  try {
    const result = await reviewPrice(Number(record.record_id), 'approve')
    await loadHistory()
    showReviewResult(result)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '审核操作失败')
  } finally {
    reviewingId.value = null
  }
}

const rejectDialogVisible = ref(false)
const rejectTarget = ref<PriceRecordDTO | null>(null)
const rejectRemark = ref('')
const rejecting = ref(false)

function openReject(record: PriceRecordDTO): void {
  rejectTarget.value = record
  rejectRemark.value = ''
  rejectDialogVisible.value = true
}

async function confirmReject(): Promise<void> {
  const target = rejectTarget.value
  if (!target) return
  if (!rejectRemark.value.trim()) {
    ElMessage.warning('驳回理由必填')
    return
  }
  rejecting.value = true
  try {
    const result = await reviewPrice(Number(target.record_id), 'reject', rejectRemark.value.trim())
    rejectDialogVisible.value = false
    await loadHistory()
    showReviewResult(result)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '审核操作失败')
  } finally {
    rejecting.value = false
  }
}

function reviewStatusType(status: string): 'success' | 'warning' | 'danger' | 'info' {
  if (status === '已通过') return 'success'
  if (status === '待审核') return 'warning'
  if (status === '已驳回') return 'danger'
  return 'info'
}

function dateLabel(value: string): string {
  return value ? value.slice(0, 10) : '—'
}

function showReviewResult(result: PriceReviewResult): void {
  lastReviewResult.value = result
  const created = result.suggestions.filter((item) => item.created)
  const children = [
    h('p', { style: 'margin:0 0 8px' }, [
      h('strong', null, `记录已${result.record.review_status}，`),
      result.took_effect
        ? h('span', { style: 'color:var(--el-color-success);font-weight:600' }, '已即时生效（价格缓存已刷新）')
        : h('span', { style: 'color:var(--el-color-warning);font-weight:600' }, '未生效（被优先级更高的当前价覆盖）'),
    ]),
    h('p', { style: 'margin:0 0 8px;color:var(--el-text-color-secondary)' }, `受影响行程：${result.affected_itinerary_ids.length} 个`),
  ]
  if (result.took_effect) {
    children.push(
      h('p', { style: 'margin:0 0 8px;color:var(--el-text-color-secondary)' }, `已生成重规划建议：${created.length} 条`),
    )
    if (created.length > 0) {
      children.push(
        h(
          'ul',
          { style: 'margin:0;padding-left:18px;color:var(--el-text-color-secondary);font-size:13px' },
          created.slice(0, 5).map((item) =>
            h('li', null, `行程 ${item.plan_id}：${item.reason ?? '建议重新规划'}${item.cost_delta ? `（费用变化 ${item.cost_delta}）` : ''}`),
          ),
        ),
      )
    }
  }
  ElMessageBox.alert(h('div', null, children), '审核结果', { confirmButtonText: '知道了' })
}
</script>

<template>
  <section class="prices">
    <div class="prices-head">
      <span class="head-title">价格审核</span>
      <el-select
        v-model="selectedPoiId"
        :loading="poisLoading"
        placeholder="选择 POI（含失效）"
        filterable
        style="width: 280px"
      >
        <el-option
          v-for="poi in pois"
          :key="poi.poi_id"
          :label="`${poi.name}（${poi.status}）`"
          :value="poi.poi_id"
        />
      </el-select>
    </div>

    <template v-if="selectedPoiId">
      <el-card v-if="currentRecord" shadow="never" class="current-card">
        <span class="current-label">当前生效价：</span>
        <span class="current-amount">¥{{ currentRecord.amount }}</span>
        <el-tag size="small" type="info">{{ currentRecord.price_type }}</el-tag>
        <span class="current-meta">来源 {{ currentRecord.source_channel }} · 采集 {{ dateLabel(currentRecord.collected_at) }} · 有效期至 {{ dateLabel(currentRecord.valid_until) }}</span>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <span class="card-title">提交新价格</span>
          <span class="card-sub">提交后进入待审核队列，游客侧暂不可见</span>
        </template>
        <el-form ref="submitRef" :model="submitForm" :rules="submitRules" label-position="top" class="submit-form">
          <div class="form-grid">
            <el-form-item label="价格类型" prop="price_type">
              <el-select v-model="submitForm.price_type" placeholder="选择类型" style="width: 100%">
                <el-option v-for="t in PRICE_TYPES" :key="t" :label="t" :value="t" />
              </el-select>
            </el-form-item>
            <el-form-item label="金额（元，免费填 0）" prop="amount">
              <el-input-number
                v-model="submitForm.amount"
                :min="0"
                :precision="2"
                :step="1"
                :controls="false"
                placeholder="如 60.00"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="来源渠道" prop="source_channel">
              <el-input v-model="submitForm.source_channel" placeholder="如 运营录入/美团" />
            </el-form-item>
            <el-form-item label="采集日期" prop="collected_at">
              <el-date-picker
                v-model="submitForm.collected_at"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="有效期至" prop="valid_until">
              <el-date-picker
                v-model="submitForm.valid_until"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                style="width: 100%"
              />
            </el-form-item>
          </div>
          <el-button type="primary" :loading="submitting" @click="submit">提交价格</el-button>
        </el-form>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <span class="card-title">待审核队列</span>
          <el-tag v-if="pendingRecords.length" size="small" type="warning">{{ pendingRecords.length }} 条待处理</el-tag>
        </template>
        <el-table v-loading="historyLoading" :data="pendingRecords" stripe>
          <el-table-column prop="price_type" label="类型" width="80" />
          <el-table-column label="金额" width="100" align="right">
            <template #default="{ row }">¥{{ row.amount }}</template>
          </el-table-column>
          <el-table-column prop="source_channel" label="来源" width="120" show-overflow-tooltip />
          <el-table-column label="采集时间" width="110">
            <template #default="{ row }">{{ dateLabel(row.collected_at) }}</template>
          </el-table-column>
          <el-table-column label="有效期至" width="110">
            <template #default="{ row }">{{ dateLabel(row.valid_until) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button link type="success" :loading="reviewingId === row.record_id" @click="approve(row)">通过</el-button>
              <el-button link type="danger" @click="openReject(row)">驳回</el-button>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty description="暂无待审核记录" :image-size="60" />
          </template>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <span class="card-title">历史价格（含未生效/已驳回）</span>
        </template>
        <el-alert v-if="historyError" type="error" :title="historyError" :closable="false" show-icon />
        <el-table v-loading="historyLoading" :data="records" stripe>
          <el-table-column prop="price_type" label="类型" width="80" />
          <el-table-column label="金额" width="100" align="right">
            <template #default="{ row }">¥{{ row.amount }}</template>
          </el-table-column>
          <el-table-column prop="source_channel" label="来源" width="120" show-overflow-tooltip />
          <el-table-column label="采集时间" width="110">
            <template #default="{ row }">{{ dateLabel(row.collected_at) }}</template>
          </el-table-column>
          <el-table-column label="有效期至" width="110">
            <template #default="{ row }">{{ dateLabel(row.valid_until) }}</template>
          </el-table-column>
          <el-table-column label="审核状态" width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="reviewStatusType(row.review_status)">{{ row.review_status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="当前价" width="80">
            <template #default="{ row }">
              <el-tag v-if="row.is_current" size="small" type="success" effect="dark">当前价</el-tag>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
          <el-table-column label="冲突说明" min-width="200">
            <template #default="{ row }">
              <el-tooltip v-if="row.conflict_note" :content="row.conflict_note" placement="top">
                <span class="conflict-note">{{ row.conflict_note }}</span>
              </el-tooltip>
              <span v-else class="muted">—</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-alert
        v-if="lastReviewResult && lastReviewResult.took_effect"
        type="success"
        :closable="false"
        show-icon
        :title="`最近一次审核已即时生效：受影响行程 ${lastReviewResult.affected_itinerary_ids.length} 个，生成重规划建议 ${lastReviewResult.suggestions.filter((s) => s.created).length} 条`"
      />
    </template>

    <el-empty v-else description="请先选择 POI，查看价格并审核" />

    <el-dialog v-model="rejectDialogVisible" title="驳回价格记录" width="420px">
      <p class="reject-tip">驳回理由必填，将写入记录的冲突说明字段：</p>
      <el-input
        v-model="rejectRemark"
        type="textarea"
        :rows="3"
        placeholder="如 与官方渠道价格不符 / 采集来源不可靠"
        maxlength="200"
        show-word-limit
      />
      <template #footer>
        <el-button @click="rejectDialogVisible = false">取消</el-button>
        <el-button type="danger" :loading="rejecting" @click="confirmReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.prices {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.prices-head {
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

.current-card :deep(.el-card__body) {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.current-label {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}

.current-amount {
  font-size: 18px;
  font-weight: 700;
  color: var(--el-color-primary);
}

.current-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.card-title {
  font-weight: 600;
}

.card-sub {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.submit-form .form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 0 16px;
}

.muted {
  color: var(--el-text-color-disabled);
}

.conflict-note {
  color: var(--el-color-warning);
  font-size: 13px;
  cursor: help;
}

.reject-tip {
  margin: 0 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
</style>