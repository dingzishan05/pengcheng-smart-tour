import type {
  ChatResponse,
  ConfirmResponse,
  ConversationItem,
  MessageItem,
  PoiDetail,
  PoiSummary,
  RefreshResponse,
  RouteOption,
  StructuredRequestPayload,
  TransportMode,
  ApiErrorPayload,
} from '@/types/api'

const BASE = '/api/v1'
const CHAT_TIMEOUT_MS = 35_000

export class ApiError extends Error {
  readonly code: number
  readonly payload: ApiErrorPayload | null

  constructor(code: number, message: string, payload: ApiErrorPayload | null = null) {
    super(message)
    this.code = code
    this.payload = payload
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

function withTimeout(outer: AbortSignal | null | undefined, ms: number): { signal: AbortSignal; cancel: () => void } {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(new DOMException('timeout', 'AbortError')), ms)
  const onOuterAbort = () => controller.abort()
  if (outer) {
    if (outer.aborted) controller.abort()
    else outer.addEventListener('abort', onOuterAbort, { once: true })
  }
  return {
    signal: controller.signal,
    cancel: () => {
      window.clearTimeout(timer)
      outer?.removeEventListener('abort', onOuterAbort)
    },
  }
}

async function parsePayload(response: Response): Promise<ApiErrorPayload | Record<string, unknown>> {
  try {
    return (await response.json()) as ApiErrorPayload | Record<string, unknown>
  } catch {
    return { code: 50005, message: '服务响应异常，请稍后重试' }
  }
}

async function rawRequest<T>(path: string, init: RequestInit, timeoutMs?: number): Promise<T> {
  const run = async (signal: AbortSignal | undefined): Promise<T> => {
    let response: Response
    try {
      response = await fetch(`${BASE}${path}`, {
        ...init,
        signal,
        credentials: 'same-origin',
      })
    } catch (error) {
      if (isAbortError(error)) {
        throw new ApiError(0, '服务繁忙，请重试')
      }
      throw new ApiError(0, '网络异常，请检查连接后重试')
    }
    if (response.status === 204) {
      return null as T
    }
    const payload = await parsePayload(response)
    if (!response.ok) {
      const body = payload as ApiErrorPayload
      const code = typeof body.code === 'number' ? body.code : response.status * 100
      throw new ApiError(code, body.message ?? '请求失败', body)
    }
    return payload as T
  }

  if (timeoutMs === undefined) {
    return run(undefined)
  }
  const { signal, cancel } = withTimeout(init.signal, timeoutMs)
  try {
    return await run(signal)
  } finally {
    cancel()
  }
}

async function request<T>(path: string, init: RequestInit, timeoutMs?: number): Promise<T> {
  try {
    return await rawRequest<T>(path, init, timeoutMs)
  } catch (error) {
    if (error instanceof ApiError && error.code === 40101) {
      return rawRequest<T>(path, init, timeoutMs)
    }
    throw error
  }
}

export function chat(
  body: { conversation_id: string | null; message: string },
  signal?: AbortSignal,
): Promise<ChatResponse> {
  return request<ChatResponse>('/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  }, CHAT_TIMEOUT_MS)
}

export function createPlans(
  conversationId: string | null,
  req: StructuredRequestPayload,
): Promise<{ conversation_id: string; plans: import('@/types/api').PlanSummary[] | null; plan_details: import('@/types/api').PlanDetail[]; data_time: string | null }> {
  return request('/plans', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, request: req }),
  })
}

export function refreshPlan(
  planId: string,
  conversationId: string | null,
  changes: Record<string, unknown>,
  trigger: 'user_change' | 'data_change' = 'user_change',
): Promise<RefreshResponse> {
  return request<RefreshResponse>(`/plans/${planId}/refresh`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trigger, changes, conversation_id: conversationId }),
  }, 20_000)
}

export function confirmPlan(
  planId: string,
  conversationId: string | null,
): Promise<ConfirmResponse> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  return request<ConfirmResponse>(`/plans/${planId}/confirm${query}`, { method: 'POST' })
}

export function getPlanDetail(planId: string, conversationId: string | null): Promise<import('@/types/api').PlanDetail> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  return request(`/plans/${planId}${query}`, {})
}

export function listPois(params: { category?: string; tag?: string; page?: number; page_size?: number } = {}): Promise<{ items: PoiSummary[]; total: number; page: number; page_size: number }> {
  const search = new URLSearchParams()
  if (params.category) search.set('category', params.category)
  if (params.tag) search.set('tag', params.tag)
  if (params.page) search.set('page', String(params.page))
  if (params.page_size) search.set('page_size', String(params.page_size))
  const qs = search.toString()
  return request(`/pois${qs ? `?${qs}` : ''}`, {})
}

export function getPoiDetail(poiId: string): Promise<PoiDetail> {
  return request(`/pois/${poiId}`, {})
}

export function queryRoutes(
  origin: string,
  destination: string,
  modes: TransportMode[],
): Promise<{ routes: RouteOption[] }> {
  const search = new URLSearchParams({ origin, destination, modes: modes.join(',') })
  return request(`/routes?${search.toString()}`, {})
}

export function listConversations(): Promise<{ items: ConversationItem[]; total: number }> {
  return request('/conversations', {})
}

export function listMessages(
  conversationId: string,
  page = 1,
  pageSize = 50,
): Promise<{ items: MessageItem[]; total: number; page: number; page_size: number }> {
  return request(`/conversations/${conversationId}/messages?page=${page}&page_size=${pageSize}`, {})
}

export function deleteConversation(conversationId: string): Promise<{ conversation_id: string; status: string }> {
  return request(`/conversations/${conversationId}`, { method: 'DELETE' })
}