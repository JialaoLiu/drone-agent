import { ref } from 'vue'
import { getSite } from '../api'
import type { Site } from '../types'

// 场地数据缓存（按场景名），多个组件共用，避免重复请求
const cache = ref<Record<string, Site>>({})
const pending: Record<string, Promise<Site | null> | undefined> = {}

export function useSites() {
  async function load(name: string | null | undefined): Promise<Site | null> {
    if (!name || name === 'eval') return null
    if (cache.value[name]) return cache.value[name]
    pending[name] ??= getSite(name).then((s) => { cache.value[name] = s; return s }).catch(() => null).finally(() => { pending[name] = undefined })
    return pending[name]!
  }
  return { cache, load }
}
