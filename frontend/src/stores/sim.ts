import { computed, ref } from 'vue'
import { emergencyReturn, getHealth, getSim, startSim, stopSim } from '../api'
import type { Preflight, SimStatus } from '../types'
import { useToast } from './toast'

// 仿真状态在所有页面之间共享：每 2 秒刷新；飞行前检查每 15 秒（只读飞控与本机状态、天气接口，不调用模型、不花钱）
const sim = ref<SimStatus>({ scene: null, camera: false, px4: false, telemetry: {}, active_mission: null })
const health = ref<Preflight | null>(null)
const healthLoading = ref(false)
const reachable = ref(true)
let started = false

async function refresh() {
  try {
    const s = await getSim()
    const stopped = !s.px4 && sim.value.px4
    sim.value = s
    reachable.value = true
    if (stopped || !s.scene) health.value = null       // 仿真停止后立即清空，不显示过期结果（DR-061）
  } catch {
    reachable.value = false
  }
}

async function refreshHealth() {
  if (!sim.value.px4 || sim.value.job?.state === 'starting') {
    health.value = null
    return
  }
  healthLoading.value = true
  try { health.value = await getHealth() } catch { health.value = null } finally { healthLoading.value = false }
}

// 仿真的综合状态：界面上只显示一个结论，避免“已就绪”和“检查无法完成”同时出现（DR-061）
export type SimPhase = 'offline' | 'stopped' | 'starting' | 'error' | 'checking' | 'ready' | 'blocked' | 'busy'
const phase = computed<SimPhase>(() => {
  const s = sim.value
  if (!reachable.value) return 'offline'
  if (s.job?.state === 'starting') return 'starting'
  if (s.job?.state === 'error' && !s.scene) return 'error'
  if (!s.scene) return 'stopped'
  if (!s.px4 || !s.camera) return 'starting'
  if (s.active_mission) return 'busy'
  if (health.value?.ok === false) return 'blocked'
  if (!health.value) return 'checking'
  return 'ready'
})
const PHASE_TEXT: Record<SimPhase, string> = {
  offline: '后端未连接', stopped: '仿真未启动', starting: '仿真启动中', error: '仿真启动失败', checking: '飞行前检查中',
  ready: '仿真就绪', blocked: '飞行前检查不通过', busy: '执行任务中',
}

export function useSim() {
  const { show } = useToast()
  if (!started) {
    started = true
    refresh().then(refreshHealth)
    window.setInterval(refresh, 2000)
    window.setInterval(refreshHealth, 15000)
  }
  // 能否下达任务：飞控与相机就绪即可；飞行前检查不通过时由安全员在规划时拦下并说明
  const ready = computed(() => !!sim.value.scene && sim.value.px4 && sim.value.camera && sim.value.job?.state !== 'starting')
  const phaseText = computed(() => (phase.value === 'starting' && sim.value.job?.stage ? `${PHASE_TEXT.starting}：${sim.value.job.stage}` : PHASE_TEXT[phase.value]))

  async function start(scene: string) {
    try {
      await startSim(scene)
      show(`正在启动场景 ${scene}，约 30–60 秒`, 'info')
      await refresh()
    } catch (e) { show((e as Error).message, 'bad') }
  }
  async function stop() {
    try { await stopSim(); health.value = null; await refresh(); show('仿真已停止', 'ok') } catch (e) { show((e as Error).message, 'bad') }
  }
  async function returnHome() {
    try { show((await emergencyReturn()).message, 'warn') } catch (e) { show((e as Error).message, 'bad') }
  }
  return { sim, health, healthLoading, reachable, ready, phase, phaseText, refresh, refreshHealth, start, stop, returnHome }
}
