import { computed, ref } from 'vue'
import { getSim } from '../api'
import type { SimStatus } from '../types'

// 仿真状态在所有页面之间共享，每 2 秒刷新一次
const sim = ref<SimStatus>({ scene: null, camera: false, px4: false, telemetry: {}, active_mission: null })
const reachable = ref(true)
let timer: number | undefined

async function refresh() {
  try {
    sim.value = await getSim()
    reachable.value = true
  } catch {
    reachable.value = false
  }
}

export function useSim() {
  if (timer === undefined) {
    refresh()
    timer = window.setInterval(refresh, 2000)
  }
  const ready = computed(() => !!sim.value.scene && sim.value.px4 && sim.value.camera)
  return { sim, ready, reachable, refresh }
}
