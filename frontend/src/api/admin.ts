import type {
  AdminLoginResponse,
  AdminPoiDTO,
  AdminPoiRow,
  AuditLogDTO,
  KnowledgeDocDTO,
  KnowledgeDocDetail,
  KnowledgeIngestResult,
  PriceRecordDTO,
  PriceReviewResult,
  PriceSubmitPayload,
  PoiStatusResult,
  SourceStatusDTO,
} from '@/types/admin'

const BASE = '/api/v1'
const TOKEN_KEY = 'pczy_admin_token'
const USERNAME_KEY = 'pczy_admin_username'

export class AdminApiError extends Error {
  readonly code: number

  constructor(code: number, message: string) {
    super(message)
    this.code = code
  }
}

export function getAdminToken(): string | null {
  return window.localStorage.getItem(TOKEN_KEY)
}

export function getAdminUsername(): string | null {
  return window.localStorage.getItem(USERNAME_KEY)
}

export function clearAdminSession(): void {
  window.localStorage.removeItem(TOKEN_KEY)
  window.localStorage.removeItem(USERNAME_KEY)
}

async function adminRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = getAdminToken()
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
      credentials: 'same-origin',
    })
  } catch {
    throw new AdminApiError(0, '网络异常，请检查连接后重试')
  }
  let payload: Record<string, unknown>
  try {
    payload = (await response.json()) as Record<string, unknown>
  } catch {
    payload = { code: 50005, message: '服务响应异常，请稍后重试' }
  }
  if (!response.ok) {
    const code = typeof payload.code === 'number' ? payload.code : response.status * 100
    if (code === 40102) {
      clearAdminSession()
    }
    throw new AdminApiError(code, typeof payload.message === 'string' ? payload.message : '请求失败')
  }
  return payload as T
}

export async function adminLogin(username: string, password: string): Promise<AdminLoginResponse> {
  const result = await adminRequest<AdminLoginResponse>('/admin/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  window.localStorage.setItem(TOKEN_KEY, result.token)
  window.localStorage.setItem(USERNAME_KEY, result.username)
  return result
}

export function submitPrice(body: PriceSubmitPayload): Promise<{ record: PriceRecordDTO }> {
  return adminRequest('/admin/prices', { method: 'POST', body: JSON.stringify(body) })
}

export function reviewPrice(
  recordId: number,
  action: 'approve' | 'reject',
  remark?: string,
): Promise<PriceReviewResult> {
  return adminRequest('/admin/prices/review', {
    method: 'POST',
    body: JSON.stringify({ record_id: recordId, action, remark }),
  })
}

export function fetchPriceHistory(poiId: string): Promise<{ current: PriceRecordDTO | null; records: PriceRecordDTO[] }> {
  return adminRequest(`/prices/${poiId}?include_hidden=true`)
}

export function createPoi(body: Record<string, unknown>): Promise<{ poi: AdminPoiDTO }> {
  return adminRequest('/admin/pois', { method: 'POST', body: JSON.stringify(body) })
}

export function updatePoi(body: Record<string, unknown>): Promise<{ poi: AdminPoiDTO }> {
  return adminRequest('/admin/pois', { method: 'PATCH', body: JSON.stringify(body) })
}

export function setPoiStatus(poiId: string, status: '有效' | '失效'): Promise<PoiStatusResult> {
  return adminRequest(`/admin/pois/${poiId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function uploadKnowledge(body: {
  title: string
  topic: string
  content: string
  source: string
}): Promise<KnowledgeIngestResult> {
  return adminRequest('/admin/knowledge', { method: 'POST', body: JSON.stringify(body) })
}

export function editKnowledge(
  documentId: number,
  body: { content: string; title?: string; topic?: string; source?: string },
): Promise<KnowledgeIngestResult> {
  return adminRequest(`/admin/knowledge/${documentId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function setKnowledgeStatus(documentId: number, status: '有效' | '失效'): Promise<{ document_id: number; status: string; title: string; version: number }> {
  return adminRequest(`/admin/knowledge/${documentId}/status`, {
    method: 'PATCH',
    body: JSON.stringify({ status }),
  })
}

export function listKnowledge(
  topic?: string,
  status?: string,
): Promise<{ items: KnowledgeDocDTO[]; total: number }> {
  const search = new URLSearchParams()
  if (topic) search.set('topic', topic)
  if (status) search.set('status', status)
  const qs = search.toString()
  return adminRequest(`/admin/knowledge${qs ? `?${qs}` : ''}`)
}

export function knowledgeVersions(title: string): Promise<{ items: KnowledgeDocDTO[]; total: number }> {
  return adminRequest(`/admin/knowledge/versions?title=${encodeURIComponent(title)}`)
}

export function fetchKnowledgeDetail(documentId: number): Promise<KnowledgeDocDetail> {
  return adminRequest(`/admin/knowledge/${documentId}`)
}

export function listAudits(params: {
  operator_id?: number
  entity_type?: string
  entity_id?: number
  since?: string
  until?: string
  page?: number
  page_size?: number
}): Promise<{ items: AuditLogDTO[]; total: number; page: number; page_size: number }> {
  const search = new URLSearchParams()
  if (params.operator_id !== undefined) search.set('operator_id', String(params.operator_id))
  if (params.entity_type) search.set('entity_type', params.entity_type)
  if (params.entity_id !== undefined) search.set('entity_id', String(params.entity_id))
  if (params.since) search.set('since', params.since)
  if (params.until) search.set('until', params.until)
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  return adminRequest(`/admin/audits?${search.toString()}`)
}

export async function fetchSourcesStatus(): Promise<{ sources: SourceStatusDTO[] }> {
  const response = await fetch(`${BASE}/sources/status`, { credentials: 'same-origin' })
  if (!response.ok) {
    throw new AdminApiError(response.status * 100, '数据源状态获取失败')
  }
  return (await response.json()) as { sources: SourceStatusDTO[] }
}

export function fetchPoisForAdmin(params: { category?: string; active?: string } = {}): Promise<{ items: AdminPoiRow[]; total: number }> {
  const search = new URLSearchParams()
  if (params.category) search.set('category', params.category)
  search.set('active', params.active ?? 'false')
  search.set('page', '1')
  search.set('page_size', '100')
  return adminRequest(`/pois?${search.toString()}`)
}