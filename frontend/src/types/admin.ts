export interface AdminLoginResponse {
  token: string
  token_type: string
  expires_in: number
  username: string
  display_name: string | null
}

export interface PriceRecordDTO {
  record_id: string
  poi_id: string
  price_type: string
  amount: string
  source_channel: string
  collected_at: string
  valid_until: string
  review_status: string
  is_current: boolean
  conflict_note: string | null
}

export interface PriceReviewResult {
  record: PriceRecordDTO
  took_effect: boolean
  affected_itinerary_ids: number[]
  suggestions: { plan_id: string; created: boolean; reason?: string; cost_delta?: string | null }[]
}

export interface PriceSubmitPayload {
  poi_id: number
  price_type: string
  amount: number
  source_channel: string
  collected_at: string
  valid_until: string
}

export interface AdminPoiDTO {
  poi_id: string
  name: string
  category: string
  tags: string[]
  location: { lng: number; lat: number }
  city: string
  visit_minutes: number
  indoor: boolean
  status: string
  description: string | null
  source_channel: string
  collected_at: string
}

export interface AdminPoiRow {
  poi_id: string
  name: string
  category: string
  tags: string[]
  location: { lng: number; lat: number }
  visit_minutes: number
  indoor: boolean
  status: string
  ticket_price?: string
}

export interface PoiStatusResult {
  poi_id: string
  status: string
  affected_itinerary_ids: number[]
  suggestions: { plan_id: string; created: boolean; reason?: string }[]
}

export interface KnowledgeDocDTO {
  document_id: number
  title: string
  topic: string
  version: number
  status: string
  ingested_at: string | null
}

export interface KnowledgeDocDetail extends KnowledgeDocDTO {
  content: string
  source: string
}

export interface KnowledgeIngestResult {
  status: string
  document_id: number
  title: string
  version: number
}

export interface AuditLogDTO {
  audit_id: string
  operator_id: string
  action: string
  entity_type: string
  entity_id: string
  before_value: Record<string, unknown> | null
  after_value: Record<string, unknown> | null
  operated_at: string | null
}

export interface SourceStatusDTO {
  name: string
  last_success_at: string | null
  availability: string
  [key: string]: unknown
}