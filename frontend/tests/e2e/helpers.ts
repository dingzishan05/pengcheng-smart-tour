import type { Page } from '@playwright/test'

export interface ChatMockBody {
  conversation_id: string
  response_type: 'clarify' | 'plans' | 'knowledge_answer' | 'fallback'
  content: string
  plans: unknown[] | null
  plan_details_available: boolean
  sources: unknown[] | null
  data_time: string | null
  code: number | null
  message: string | null
}

export const POI_ITEMS = [
  {
    poi_id: '1',
    name: '龟山汉墓',
    category: '景点',
    tags: ['历史', '汉文化'],
    location: { lng: 117.15, lat: 34.29 },
    visit_minutes: 90,
    indoor: false,
    status: '有效',
    ticket_price: '60.00',
    price_meta: {
      source_channel: '官方渠道',
      collected_at: '2026-10-01T00:00:00+00:00',
      is_reference: false,
    },
  },
  {
    poi_id: '2',
    name: '汉文化景区',
    category: '景点',
    tags: ['历史', '汉文化'],
    location: { lng: 117.19, lat: 34.27 },
    visit_minutes: 150,
    indoor: false,
    status: '有效',
    ticket_price: '90.00',
    price_meta: {
      source_channel: '官方渠道',
      collected_at: '2026-10-01T00:00:00+00:00',
      is_reference: false,
    },
  },
  {
    poi_id: '3',
    name: '云龙湖',
    category: '景点',
    tags: ['自然'],
    location: { lng: 117.18, lat: 34.24 },
    visit_minutes: 120,
    indoor: false,
    status: '有效',
    ticket_price: '0.00',
    price_meta: {
      source_channel: '官方渠道',
      collected_at: '2026-10-01T00:00:00+00:00',
      is_reference: false,
    },
  },
]

export function makePlanSummary(
  planId: string,
  planType: '省钱' | '省时' | '均衡',
  totalCost: string,
  totalMinutes: number,
  transportMinutes: number,
): Record<string, unknown> {
  return {
    plan_id: planId,
    plan_type: planType,
    status: '草稿',
    total_cost: totalCost,
    total_minutes: totalMinutes,
    transport_minutes: transportMinutes,
    highlights: ['路线紧凑', '覆盖兴趣点'],
    suited_for: '首次到访的游客',
  }
}

export function makePlanDetail(
  planId: string,
  planType: '省钱' | '省时' | '均衡',
  totalCost: string,
): Record<string, unknown> {
  return {
    ...makePlanSummary(planId, planType, totalCost, 540, 80),
    items: [
      {
        seq: 1,
        activity_type: '游览',
        poi_id: '1',
        start_at: '2026-10-21T09:00:00',
        end_at: '2026-10-21T10:30:00',
        transport_mode: '公交',
        transport_minutes: 40,
        estimated_cost: '60.00',
        cost_meta: {
          source_channel: '官方渠道',
          collected_at: '2026-10-01T00:00:00+00:00',
          is_reference: false,
        },
      },
      {
        seq: 2,
        activity_type: '游览',
        poi_id: '2',
        start_at: '2026-10-21T11:00:00',
        end_at: '2026-10-21T13:30:00',
        transport_mode: '公交',
        transport_minutes: 25,
        estimated_cost: '90.00',
        cost_meta: {
          source_channel: '官方渠道',
          collected_at: '2026-10-01T00:00:00+00:00',
          is_reference: false,
        },
      },
    ],
    request_snapshot: { budget: '300', duration_hours: 8 },
    explanation: `${planType}型行程说明`,
    solver_note: null,
    data_time: '2026-10-20T08:30:00+00:00',
    version_lineage: 1,
  }
}

export function makePlansChatResponse(): ChatMockBody {
  return {
    conversation_id: '1',
    response_type: 'plans',
    content: '已为您规划好三种方案，可在右侧对比选择',
    plans: [
      makePlanSummary('1', '省钱', '188.00', 540, 95),
      makePlanSummary('2', '省时', '320.00', 480, 60),
      makePlanSummary('3', '均衡', '240.00', 520, 80),
    ],
    plan_details_available: true,
    sources: null,
    data_time: '2026-10-20T08:30:00+00:00',
    code: null,
    message: null,
  }
}

export function makeRefreshResponse(): Record<string, unknown> {
  return {
    new_plan: makePlanDetail('6', '省钱', '148.00'),
    old_plan: makePlanDetail('1', '省钱', '188.00'),
    diff: {
      removed: ['龟山汉墓'],
      added: ['汉文化景区'],
      cost_delta: '-40.00',
      reason: '预算由300调整为150',
    },
    conflict: null,
    data_time: '2026-10-20T09:00:00+00:00',
  }
}

export async function installBaseMocks(page: Page): Promise<void> {
  await page.route('**webapi.amap.com/**', (route) => route.abort())
  await page.route('**/api/v1/pois**', (route) =>
    route.fulfill({
      json: { items: POI_ITEMS, total: POI_ITEMS.length, page: 1, page_size: 200 },
    }),
  )
  await page.route('**/api/v1/conversations', (route) =>
    route.fulfill({ json: { items: [], total: 0 } }),
  )
  await page.route('**/api/v1/conversations/*/messages*', (route) =>
    route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 100 } }),
  )
  await page.route('**/api/v1/plans/*', (route) => {
    const url = new URL(route.request().url())
    const segments = url.pathname.split('/')
    const planId = segments[segments.length - 1] ?? '1'
    const known: Record<string, ['省钱' | '省时' | '均衡', string]> = {
      '1': ['省钱', '188.00'],
      '2': ['省时', '320.00'],
      '3': ['均衡', '240.00'],
      '6': ['省钱', '148.00'],
    }
    const [planType, cost] = known[planId] ?? ['省钱', '188.00']
    return route.fulfill({ json: makePlanDetail(planId, planType, cost) })
  })
}

export async function installChatQueue(
  page: Page,
  responses: Array<ChatMockBody | { status: number; body: Record<string, unknown> }>,
): Promise<void> {
  const queue = [...responses]
  await page.route('**/api/v1/chat', async (route) => {
    const next = queue.shift() ?? responses[responses.length - 1]
    if ('status' in next) {
      await route.fulfill({ status: next.status, json: next.body })
    } else {
      await route.fulfill({ json: next })
    }
  })
}