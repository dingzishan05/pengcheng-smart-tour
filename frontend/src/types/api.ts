export type PlanType = '省钱' | '省时' | '均衡'
export type ActivityType = '游览' | '用餐' | '交通'
export type TransportMode = '公交' | '步行' | '驾车'
export type ResponseType = 'clarify' | 'plans' | 'knowledge_answer' | 'fallback'
export type PlanStatus = '草稿' | '已确认' | '已被替换' | '挂起建议'

export interface ApiErrorPayload {
  code: number
  message: string
  detail?: {
    advice?: { min_budget_suggestion?: string | null; reasons?: string[] }
    [key: string]: unknown
  }
  session_id?: string
  plan_id?: string
}

export interface CostMeta {
  source_channel: string
  collected_at: string | null
  is_reference: boolean
}

export interface ItineraryItemDTO {
  seq: number
  activity_type: ActivityType
  poi_id: string | null
  start_at: string | null
  end_at: string | null
  transport_mode: TransportMode | null
  transport_minutes: number | null
  estimated_cost: string
  cost_meta: CostMeta
}

export interface PlanSummary {
  plan_id: string
  plan_type: PlanType
  status: PlanStatus
  total_cost: string
  total_minutes: number
  transport_minutes: number
  highlights: string[]
  suited_for: string
}

export interface PlanDetail extends PlanSummary {
  items: ItineraryItemDTO[]
  request_snapshot: Record<string, unknown>
  explanation: string
  solver_note: string | null
  data_time: string | null
  version_lineage: number | null
}

export interface SourceRef {
  doc_title: string
  source: string
}

export interface ChatResponse {
  conversation_id: string
  response_type: ResponseType
  content: string
  plans: PlanSummary[] | null
  plan_details_available: boolean
  sources: SourceRef[] | null
  data_time: string | null
  code: number | null
  message: string | null
}

export interface PlanDiff {
  removed: string[]
  added: string[]
  cost_delta: string
  reason: string
}

export interface RefreshConflict {
  advice?: { min_budget_suggestion?: string | null; reasons?: string[] }
}

export interface RefreshResponse {
  new_plan: PlanDetail
  old_plan: PlanDetail
  diff: PlanDiff
  conflict: RefreshConflict | null
}

export interface ConfirmResponse {
  plan_id: string
  status: string
  plan_detail: PlanDetail
}

export interface PoiSummary {
  poi_id: string
  name: string
  category: string
  tags: string[]
  location: { lng: number; lat: number }
  visit_minutes: number
  indoor: boolean
  status?: string
  ticket_price: string
  price_meta: CostMeta
}

export interface RouteOption {
  mode: TransportMode
  duration_minutes: number | null
  distance_meters: number | null
  unavailable?: boolean
  reason?: string | null
  cost_meta?: CostMeta
}

export interface ConversationItem {
  conversation_id: string
  title: string | null
  status: string
  last_active_at: string | null
  pending_suggestion_count: number
}

export interface MessageItem {
  message_id: string
  role: 'user' | 'system'
  content: string
  response_type: ResponseType | null
  plan_refs: string[] | null
  sent_at: string | null
}

export interface PoiDetail extends PoiSummary {
  description: string | null
  hours: { day_of_week: number; open_time: string | null; close_time: string | null; closed: boolean }[]
  city?: string
  source_channel?: string
  collected_at?: string
}

export interface StructuredRequestPayload {
  duration_hours?: number
  budget?: string | number
  interests?: string[]
  companions?: string
  pinned_pois?: string[]
  short_trip?: boolean
  visit_date?: string
}