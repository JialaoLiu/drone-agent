import { computed, ref } from 'vue'
import { getHealth, getSim } from '../api'
import type { Preflight, SimStatus } from '../types'

// 仿真状态在所有页面之间共享，每 2 秒刷新一次；飞行前检查每 15 秒一次（每次需要约 2 秒采样仿真速度）
const sim = ref<SimStatus>({ scene: null, camera: false, px4: false, telemetry: {}, active_mission: null })
const health = ref<Preflight | null>(null)
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

async function refreshHealth() {
  if (!sim.value.px4) {
    health.value = null
    return
  }
  try { health.value = await getHealth() } catch { health.value = null }
}

export function useSim() {
  if (timer === undefined) {
    refresh().then(refreshHealth)
    timer = window.setInterval(refresh, 2000)
    window.setInterval(refreshHealth, 15000)
  }
  const ready = computed(() => !!sim.value.scene && sim.value.px4 && sim.value.camera)
  return { sim, ready, reachable, refresh, health, refreshHealth }
}
