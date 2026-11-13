<script setup lang="ts">
import type { KnowledgeDocDTO, KnowledgeIngestResult } from '@/types/admin'
import type { FormInstance, FormRules } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  editKnowledge,
  fetchKnowledgeDetail,
  knowledgeVersions,
  listKnowledge,
  setKnowledgeStatus,
  uploadKnowledge,
} from '@/api/admin'

const TOPICS = ['两汉文化', '景点介绍', '特色美食', '交通常识']

const documents = ref<KnowledgeDocDTO[]>([])
const loading = ref(false)
const loadError = ref<string | null>(null)

const topicFilter = ref('')
const statusFilter = ref('')

const filteredDocuments = computed<KnowledgeDocDTO[]>(() =>
  documents.value.filter((doc) => {
    if (topicFilter.value && doc.topic !== topicFilter.value) return false
    if (statusFilter.value && doc.status !== statusFilter.value) return false
    return true
  }),
)

async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const result = await listKnowledge()
    documents.value = result.items
  } catch (error) {
    loadError.value = error instanceof Error ? error.message : '知识文档列表获取失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())

interface UploadForm {
  title: string
  topic: string
  content: string
  source: string
}

const uploadRef = ref<FormInstance>()
const uploading = ref(false)
const uploadForm = reactive<UploadForm>({
  title: '',
  topic: '',
  content: '',
  source: '',
})

const uploadRules: FormRules<UploadForm> = {
  title: [{ required: true, message: '标题必填', trigger: 'blur' }],
  topic: [{ required: true, message: `主题必须是 ${TOPICS.join('/')} 之一`, trigger: 'change' }],
  content: [
    { required: true, message: '正文必填', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) =>
        callback(value && value.trim().length >= 20 ? undefined : new Error('正文不能为空且至少 20 字')),
      trigger: 'blur',
    },
  ],
  source: [{ required: true, message: '来源必填（无出处内容禁止入库）', trigger: 'blur' }],
}

const lastIngest = ref<KnowledgeIngestResult | null>(null)

async function submitUpload(): Promise<void> {
  const inst = uploadRef.value
  if (!inst) return
  const valid = await inst.validate().catch(() => false)
  if (!valid) return
  uploading.value = true
  try {
    const result = await uploadKnowledge({
      title: uploadForm.title.trim(),
      topic: uploadForm.topic,
      content: uploadForm.content,
      source: uploadForm.source.trim(),
    })
    lastIngest.value = result
    ElMessage.success(`知识文档已入库（版本 v${result.version}），已进入检索库`)
    uploadForm.title = ''
    uploadForm.content = ''
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '入库失败')
  } finally {
    uploading.value = false
  }
}

async function toggleStatus(doc: KnowledgeDocDTO): Promise<void> {
  const target = doc.status === '有效' ? '失效' : '有效'
  const tip =
    target === '失效'
      ? '失效后立即不参与检索（向量缓存同步刷新）。'
      : '恢复后重新可被检索。'
  try {
    await ElMessageBox.confirm(`确认将「${doc.title} v${doc.version}」设为${target}？${tip}`, '状态确认', {
      confirmButtonText: target === '失效' ? '设为失效' : '恢复有效',
      cancelButtonText: '取消',
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await setKnowledgeStatus(doc.document_id, target)
    ElMessage.success(`已设为${target}，${target === '失效' ? '立即不参与检索' : '重新可检索'}`)
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '状态变更失败')
  }
}

const editVisible = ref(false)
const editTarget = ref<KnowledgeDocDTO | null>(null)
const editFormRef = ref<FormInstance>()
const editSaving = ref(false)
const editForm = reactive<{ title: string; topic: string; content: string; source: string }>({
  title: '',
  topic: '',
  content: '',
  source: '',
})

const editRules: FormRules<{ title: string; topic: string; content: string; source: string }> = {
  title: [{ required: true, message: '标题必填', trigger: 'blur' }],
  topic: [{ required: true, message: '主题必填', trigger: 'change' }],
  content: [
    { required: true, message: '正文必填', trigger: 'blur' },
    {
      validator: (_rule, value: string, callback) =>
        callback(value && value.trim().length >= 20 ? undefined : new Error('正文不能为空且至少 20 字')),
      trigger: 'blur',
    },
  ],
  source: [{ required: true, message: '来源必填', trigger: 'blur' }],
}

async function openEdit(doc: KnowledgeDocDTO): Promise<void> {
  editTarget.value = doc
  editVisible.value = true
  try {
    const detail = await fetchKnowledgeDetail(doc.document_id)
    editForm.title = detail.title
    editForm.topic = detail.topic
    editForm.content = detail.content
    editForm.source = detail.source
  } catch (error) {
    editVisible.value = false
    ElMessage.error(error instanceof Error ? error.message : '文档详情获取失败')
  }
}

async function submitEdit(): Promise<void> {
  const target = editTarget.value
  const inst = editFormRef.value
  if (!target || !inst) return
  const valid = await inst.validate().catch(() => false)
  if (!valid) return
  editSaving.value = true
  try {
    const result = await editKnowledge(target.document_id, {
      title: editForm.title.trim(),
      topic: editForm.topic,
      content: editForm.content,
      source: editForm.source.trim(),
    })
    ElMessage.success(`已保存为新版本 v${result.version}（旧版本保留可追溯）`)
    editVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    editSaving.value = false
  }
}

interface DiffLine {
  type: 'same' | 'add' | 'del'
  text: string
}

function lineDiff(oldText: string, newText: string): { left: DiffLine[]; right: DiffLine[] } {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const m = a.length
  const n = b.length
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array<number>(n + 1).fill(0))
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const seq: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      seq.push({ type: 'same', text: a[i] })
      i++
      j++
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      seq.push({ type: 'del', text: a[i] })
      i++
    } else {
      seq.push({ type: 'add', text: b[j] })
      j++
    }
  }
  while (i < m) {
    seq.push({ type: 'del', text: a[i] })
    i++
  }
  while (j < n) {
    seq.push({ type: 'add', text: b[j] })
    j++
  }
  return {
    left: seq.filter((line) => line.type !== 'add'),
    right: seq.filter((line) => line.type !== 'del'),
  }
}

const versionsVisible = ref(false)
const versionsTitle = ref('')
const versions = ref<KnowledgeDocDTO[]>([])
const versionsLoading = ref(false)
const oldVersionId = ref<number | null>(null)
const newVersionId = ref<number | null>(null)
const diffLoading = ref(false)
const diffLeft = ref<DiffLine[]>([])
const diffRight = ref<DiffLine[]>([])
const diffOldLabel = ref('')
const diffNewLabel = ref('')

async function openVersions(doc: KnowledgeDocDTO): Promise<void> {
  versionsTitle.value = doc.title
  versionsVisible.value = true
  versionsLoading.value = true
  diffLeft.value = []
  diffRight.value = []
  try {
    const result = await knowledgeVersions(doc.title)
    versions.value = result.items
    if (result.items.length >= 2) {
      newVersionId.value = result.items[0].document_id
      oldVersionId.value = result.items[1].document_id
      await runDiff()
    } else {
      oldVersionId.value = null
      newVersionId.value = result.items[0]?.document_id ?? null
    }
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '版本列表获取失败')
  } finally {
    versionsLoading.value = false
  }
}

async function runDiff(): Promise<void> {
  if (oldVersionId.value === null || newVersionId.value === null) return
  diffLoading.value = true
  try {
    const [oldDoc, newDoc] = await Promise.all([
      fetchKnowledgeDetail(oldVersionId.value),
      fetchKnowledgeDetail(newVersionId.value),
    ])
    const { left, right } = lineDiff(oldDoc.content, newDoc.content)
    diffLeft.value = left
    diffRight.value = right
    diffOldLabel.value = `v${oldDoc.version}（${oldDoc.status}）`
    diffNewLabel.value = `v${newDoc.version}（${newDoc.status}）`
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '版本内容获取失败')
  } finally {
    diffLoading.value = false
  }
}

function statusType(status: string): 'success' | 'danger' | 'info' {
  if (status === '有效') return 'success'
  if (status === '失效') return 'danger'
  return 'info'
}

function timeLabel(value: string | null): string {
  return value ? value.slice(0, 19).replace('T', ' ') : '—'
}
</script>

<template>
  <section class="knowledge">
    <div class="knowledge-head">
      <span class="head-title">知识文档管理</span>
      <div class="head-filters">
        <el-select v-model="topicFilter" placeholder="全部主题" clearable style="width: 130px">
          <el-option v-for="topic in TOPICS" :key="topic" :label="topic" :value="topic" />
        </el-select>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 110px">
          <el-option label="有效" value="有效" />
          <el-option label="失效" value="失效" />
        </el-select>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-card shadow="never">
      <template #header>
        <span class="card-title">上传知识文档</span>
        <span class="card-sub">入库管道：校验 → 清洗分块 → 向量化 → 检索库更新</span>
      </template>
      <el-form ref="uploadRef" :model="uploadForm" :rules="uploadRules" label-position="top">
        <div class="form-grid">
          <el-form-item label="标题" prop="title">
            <el-input v-model="uploadForm.title" placeholder="如 云龙湖景区游览指南" maxlength="60" />
          </el-form-item>
          <el-form-item label="主题" prop="topic">
            <el-select v-model="uploadForm.topic" placeholder="选择主题" style="width: 100%">
              <el-option v-for="topic in TOPICS" :key="topic" :label="topic" :value="topic" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="正文（至少 20 字）" prop="content">
          <el-input
            v-model="uploadForm.content"
            type="textarea"
            :rows="5"
            placeholder="输入文档正文，入库时将自动清洗、按段落分块并生成向量"
          />
        </el-form-item>
        <div class="upload-foot">
          <el-form-item label="来源" prop="source" class="source-item">
            <el-input v-model="uploadForm.source" placeholder="如 徐州文旅官网（无出处禁止入库）" />
          </el-form-item>
          <el-button type="primary" :loading="uploading" class="upload-btn" @click="submitUpload">
            {{ uploading ? '管道处理中…' : '提交入库' }}
          </el-button>
        </div>
      </el-form>
      <div v-if="lastIngest" class="ingest-result">
        <el-steps :active="4" align-center finish-status="success" class="pipeline-steps">
          <el-step title="校验" />
          <el-step title="清洗分块" />
          <el-step title="向量化" />
          <el-step title="入库" />
        </el-steps>
        <p class="ingest-meta">
          管道状态：{{ lastIngest.status }} · 文档 ID {{ lastIngest.document_id }} · 「{{ lastIngest.title }}」版本 v{{ lastIngest.version }}
        </p>
      </div>
    </el-card>

    <el-alert v-if="loadError" type="error" :title="loadError" :closable="false" show-icon />

    <el-card shadow="never">
      <template #header>
        <span class="card-title">文档列表</span>
        <el-tag size="small" class="total-tag">共 {{ filteredDocuments.length }} 篇</el-tag>
      </template>
      <el-table v-loading="loading" :data="filteredDocuments" stripe>
        <el-table-column prop="title" label="标题" min-width="180" show-overflow-tooltip />
        <el-table-column prop="topic" label="主题" width="110" />
        <el-table-column label="版本" width="80">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="入库时间" width="160">
          <template #default="{ row }">{{ timeLabel(row.ingested_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="220" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openVersions(row)">版本与对比</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑新版本</el-button>
            <el-button link :type="row.status === '有效' ? 'danger' : 'success'" @click="toggleStatus(row)">
              {{ row.status === '有效' ? '失效' : '恢复' }}
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="暂无知识文档" />
        </template>
      </el-table>
    </el-card>

    <el-dialog v-model="editVisible" :title="`编辑新版本（当前 v${editTarget?.version ?? '—'}，保存后生成新版本）`" width="640px" destroy-on-close>
      <el-form ref="editFormRef" :model="editForm" :rules="editRules" label-position="top">
        <div class="form-grid">
          <el-form-item label="标题" prop="title">
            <el-input v-model="editForm.title" />
          </el-form-item>
          <el-form-item label="主题" prop="topic">
            <el-select v-model="editForm.topic" style="width: 100%">
              <el-option v-for="topic in TOPICS" :key="topic" :label="topic" :value="topic" />
            </el-select>
          </el-form-item>
        </div>
        <el-form-item label="正文" prop="content">
          <el-input v-model="editForm.content" type="textarea" :rows="7" />
        </el-form-item>
        <el-form-item label="来源" prop="source">
          <el-input v-model="editForm.source" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="editSaving" @click="submitEdit">保存为新版本</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="versionsVisible" :title="`版本记录：${versionsTitle}`" width="860px" top="6vh">
      <div v-loading="versionsLoading" class="versions-body">
        <div class="version-selectors">
          <span class="selector-label">对比版本：</span>
          <el-select v-model="oldVersionId" placeholder="旧版本" size="small" style="width: 160px" @change="runDiff">
            <el-option v-for="v in versions" :key="v.document_id" :label="`v${v.version}（${v.status}）`" :value="v.document_id" />
          </el-select>
          <span class="selector-arrow">→</span>
          <el-select v-model="newVersionId" placeholder="新版本" size="small" style="width: 160px" @change="runDiff">
            <el-option v-for="v in versions" :key="v.document_id" :label="`v${v.version}（${v.status}）`" :value="v.document_id" />
          </el-select>
        </div>
        <el-table :data="versions" size="small" stripe class="version-table">
          <el-table-column label="版本" width="70">
            <template #default="{ row }">v{{ row.version }}</template>
          </el-table-column>
          <el-table-column prop="topic" label="主题" width="110" />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="入库时间" width="160">
            <template #default="{ row }">{{ timeLabel(row.ingested_at) }}</template>
          </el-table-column>
        </el-table>
        <div v-if="diffLeft.length || diffRight.length" v-loading="diffLoading" class="diff-wrap">
          <div class="diff-head">
            <span>{{ diffOldLabel }}</span>
            <span>{{ diffNewLabel }}</span>
          </div>
          <div class="diff-columns">
            <div class="diff-col">
              <div v-for="(line, idx) in diffLeft" :key="idx" :class="`diff-line line-${line.type}`">
                {{ line.text || ' ' }}
              </div>
            </div>
            <div class="diff-col">
              <div v-for="(line, idx) in diffRight" :key="idx" :class="`diff-line line-${line.type}`">
                {{ line.text || ' ' }}
              </div>
            </div>
          </div>
          <p class="diff-legend">红色行仅存在于旧版本，绿色行仅存在于新版本（行级对比）</p>
        </div>
      </div>
    </el-dialog>
  </section>
</template>

<style scoped>
.knowledge {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.knowledge-head {
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
}

.card-title {
  font-weight: 600;
}

.card-sub {
  margin-left: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.form-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}

.upload-foot {
  display: flex;
  align-items: flex-end;
  gap: 16px;
}

.source-item {
  flex: 1;
}

.upload-btn {
  margin-bottom: 18px;
}

.ingest-result {
  margin-top: 8px;
  padding-top: 12px;
  border-top: 1px dashed var(--el-border-color);
}

.pipeline-steps {
  margin-bottom: 4px;
}

.ingest-meta {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: center;
}

.total-tag {
  margin-left: 10px;
}

.versions-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  min-height: 120px;
}

.version-selectors {
  display: flex;
  align-items: center;
  gap: 8px;
}

.selector-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.selector-arrow {
  color: var(--el-text-color-secondary);
}

.diff-wrap {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  overflow: hidden;
}

.diff-head {
  display: grid;
  grid-template-columns: 1fr 1fr;
  background: var(--el-fill-color-light);
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.diff-head span {
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.diff-head span + span {
  border-left: 1px solid var(--el-border-color-lighter);
}

.diff-columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  max-height: 320px;
  overflow: auto;
}

.diff-col {
  padding: 8px 0;
  overflow: hidden;
}

.diff-col + .diff-col {
  border-left: 1px solid var(--el-border-color-lighter);
}

.diff-line {
  padding: 0 12px;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
}

.line-del {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.line-add {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.diff-legend {
  margin: 0;
  padding: 6px 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
}
</style>