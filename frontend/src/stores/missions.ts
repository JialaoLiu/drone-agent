import { computed, ref } from 'vue'
import { cancelMission, createMission, LIVE_STATUSES, listMissions, startTroubleshoot } from '../api'
import type { MissionSummary } from '../types'
import { useToast } from './toast'

// 任务列表在工作台左侧和诊断页共用：每 3 秒刷新，状态变化（完成、取消、排队开始）自动反映（1.35）
const missions = ref<MissionSummary[]>([])
const loaded = ref(false)
let started = false

async function refresh() {
  try { missions.value = await listMissions(); loaded.value = true } catch { /* 后端暂时不可用时保留上次的列表 */ }
}

export function useMissions() {
  const { show } = useToast()
  if (!started) {
    started = true
    refresh()
    window.setInterval(refresh, 3000)
  }
  const live = computed(() => missions.value.filter((m) => LIVE_STATUSES.includes(m.status)))

  async function create(task: string, autoApprove: boolean): Promise<string | null> {
    try {
      const r = await createMission(task, autoApprove)
      if (r.queued) show(`已加入队列，前面还有 ${r.ahead} 个任务；轮到时自动开始`, 'info', 6000)
      await refresh()
      return r.id
    } catch (e) { show((e as Error).message, 'bad'); return null }
  }
  async function cancel(id: string) {
    try { await cancelMission(id); show('已取消；飞行中会立即返航', 'warn'); await refresh() } catch (e) { show((e as Error).message, 'bad') }
  }
  async function troubleshoot(symptom: string, fault: string | null): Promise<string | null> {
    try { const { id } = await startTroubleshoot(symptom, fault); await refresh(); return id } catch (e) { show((e as Error).message, 'bad'); return null }
  }
  return { missions, loaded, live, refresh, create, cancel, troubleshoot }
}
