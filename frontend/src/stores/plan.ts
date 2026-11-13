import { defineStore } from 'pinia'
import { ref } from 'vue'
import { ApiError, confirmPlan, getPlanDetail, listPois, refreshPlan } from '@/api/client'
import { useSessionStore } from '@/stores/session'
import type { PlanDetail, PlanSummary, PoiSummary, RefreshResponse } from '@/types/api'

export interface InfeasibleInfo {
  reasons: string[]
  minBudgetSuggestion: string | null
}

export interface ChangeNotice {
  planId: string
  reason: string
  summary: string
  diff: RefreshResponse | null
}

export const usePlanStore = defineStore('plan', () => {
  const plans = ref<PlanSummary[]>([])
  const selectedPlanId = ref<string | null>(null)
  const selectedDetail = ref<PlanDetail | null>(null)
  const detailLoading = ref(false)
  const dataTime = ref<string | null>(null)
  const pendingDiff = ref<RefreshResponse | null>(null)
  const notices = ref<ChangeNotice[]>([])
  const infeasible = ref<InfeasibleInfo | null>(null)
  const refreshError = ref<string | null>(null)
  const poiMap = ref<Record<string, PoiSummary>>({})

  async function loadPois(): Promise<void> {
    // 后端 page_size 上限为 100，传 200 会直接 422，导致 poiMap 为空、
    // 时间轴与地图只能显示 “POI-5” 这类编号。这里按 100 分页取全量。
    const map: Record<string, PoiSummary> = {}
    let page = 1
    for (;;) {
      const result = await listPois({ page, page_size: 100 })
      for (const poi of result.items) {
        map[poi.poi_id] = poi
      }
      if (result.items.length === 0 || page * result.page_size >= result.total || page >= 20) {
        break
      }
      page += 1
    }
    poiMap.value = map
  }

  async function restoreFromRefs(planRefs: string[]): Promise<void> {
    const details = await Promise.all(
      planRefs.slice(0, 3).map((id) =>
        getPlanDetail(id, useSessionStore().activeConversationId).catch(() => null),
      ),
    )
    const valid = details.filter((d): d is PlanDetail => d !== null)
    if (valid.length === 0) return
    plans.value = valid.map((d) => ({
      plan_id: d.plan_id,
      plan_type: d.plan_type,
      status: d.status,
      total_cost: d.total_cost,
      total_minutes: d.total_minutes,
      transport_minutes: d.transport_minutes,
      highlights: d.highlights,
      suited_for: d.suited_for,
    }))
    selectedPlanId.value = plans.value[0]?.plan_id ?? null
    selectedDetail.value = valid[0] ?? null
    dataTime.value = valid[0]?.data_time ?? null
  }

  function loadPlans(list: PlanSummary[], dataTimeValue: string | null): void {
    plans.value = list
    dataTime.value = dataTimeValue
    const stillExists = list.some((p) => p.plan_id === selectedPlanId.value)
    const target = stillExists ? selectedPlanId.value : list[0]?.plan_id ?? null
    selectedPlanId.value = target
    if (target !== null) {
      void selectPlan(target)
    } else {
      selectedDetail.value = null
    }
  }

  async function selectPlan(planId: string): Promise<void> {
    selectedPlanId.value = planId
    detailLoading.value = true
    try {
      selectedDetail.value = await getPlanDetail(planId, useSessionStore().activeConversationId)
    } catch {
      selectedDetail.value = null
    } finally {
      detailLoading.value = false
    }
  }

  async function refresh(
    planId: string,
    changes: Record<string, unknown>,
    trigger: 'user_change' | 'data_change' = 'user_change',
  ): Promise<void> {
    infeasible.value = null
    refreshError.value = null
    try {
      pendingDiff.value = await refreshPlan(
        planId,
        useSessionStore().activeConversationId,
        changes,
        trigger,
      )
    } catch (error) {
      if (error instanceof ApiError && error.code === 50001) {
        const advice = error.payload?.detail?.advice
        infeasible.value = {
          reasons: advice?.reasons ?? ['当前约束下无可行行程，请放宽预算或时长'],
          minBudgetSuggestion: advice?.min_budget_suggestion ?? null,
        }
      } else {
        refreshError.value = '数据刷新失败，当前展示为上次结果'
      }
    }
  }

  async function confirmPending(): Promise<void> {
    const diff = pendingDiff.value
    if (!diff) return
    await confirmPlan(diff.new_plan.plan_id, useSessionStore().activeConversationId)
    pendingDiff.value = null
    await selectPlan(diff.new_plan.plan_id)
    await useSessionStore().refresh()
  }

  function dismissDiff(): void {
    pendingDiff.value = null
  }

  function pushNotice(notice: ChangeNotice): void {
    notices.value.push(notice)
  }

  function ackNotice(planId: string): ChangeNotice | null {
    const index = notices.value.findIndex((n) => n.planId === planId)
    if (index < 0) return null
    return notices.value.splice(index, 1)[0] ?? null
  }

  function reset(): void {
    plans.value = []
    selectedPlanId.value = null
    selectedDetail.value = null
    pendingDiff.value = null
    infeasible.value = null
    refreshError.value = null
    dataTime.value = null
  }

  return {
    plans,
    selectedPlanId,
    selectedDetail,
    detailLoading,
    dataTime,
    pendingDiff,
    notices,
    infeasible,
    refreshError,
    poiMap,
    loadPois,
    restoreFromRefs,
    loadPlans,
    selectPlan,
    refresh,
    confirmPending,
    dismissDiff,
    pushNotice,
    ackNotice,
    reset,
  }
})