import { defineStore } from 'pinia'
import { ref } from 'vue'
import { queryRoutes } from '@/api/client'
import type { RouteOption, TransportMode } from '@/types/api'

export const useMapStore = defineStore('map', () => {
  const mode = ref<TransportMode>('公交')
  const compareRoutes = ref<RouteOption[]>([])
  const routesLoading = ref(false)
  const routesError = ref<string | null>(null)

  async function switchMode(nextMode: TransportMode, origin: string, destination: string): Promise<void> {
    mode.value = nextMode
    if (!origin || !destination) {
      compareRoutes.value = []
      return
    }
    routesLoading.value = true
    routesError.value = null
    try {
      const result = await queryRoutes(origin, destination, ['公交', '步行', '驾车'])
      compareRoutes.value = result.routes
    } catch {
      routesError.value = '路线数据暂时不可用，请稍后重试'
      compareRoutes.value = []
    } finally {
      routesLoading.value = false
    }
  }

  function reset(): void {
    mode.value = '公交'
    compareRoutes.value = []
    routesError.value = null
  }

  return { mode, compareRoutes, routesLoading, routesError, switchMode, reset }
})