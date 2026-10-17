import { computed, onBeforeUnmount, ref, watch, type Ref } from 'vue'
import { eventsUrl, getMission, getScore, LIVE_STATUSES } from '../api'
import type { Mission, MissionEvent, Telemetry } from '../types'

// 一个任务的实时数据：任务记录、事件流（WebSocket，断线重连）、遥测、对照标准答案的得分。
// 切换任务时旧任务的异步结果与事件一律丢弃（深度审查 H10）。
export function useTaskStream(id: Ref<string | null>) {
  const mission = ref<Mission | null>(null)
  const events = ref<MissionEvent[]>([])
  const telemetry = ref<Partial<Telemetry>>({})
  const score = ref<Record<string, any> | null>(null)
  const error = ref('')
  let socket: WebSocket | undefined
  let lastSeq = 0
  let timer: number | undefined
  let reconnect: number | undefined
  let generation = 0
  let unmounted = false

  const live = computed(() => !!mission.value && LIVE_STATUSES.includes(mission.value.status))
  const isTroubleshoot = computed(() => (mission.value?.brief as { type?: string } | null)?.type === 'troubleshoot')

  function closeSocket() {
    window.clearTimeout(reconnect)
    if (socket) { socket.onclose = null; socket.onmessage = null; socket.close(); socket = undefined }
  }

  async function loadScore(gen: number) {
    if (!mission.value?.result || isTroubleshoot.value) return
    const s = await getScore(mission.value.id).catch(() => null)
    if (gen === generation) score.value = s
  }

  function refresh() {
    const gen = generation
    window.clearTimeout(timer)
    timer = window.setTimeout(async () => {
      if (!id.value || gen !== generation) return
      const m = await getMission(id.value).catch(() => null)
      if (!m || gen !== generation) return
      mission.value = m
      if (!live.value) await loadScore(gen)
    }, 300)
  }

  function connect(mid: string, gen: number) {
    if (unmounted || gen !== generation) return
    socket = new WebSocket(eventsUrl(mid, lastSeq))
    socket.onmessage = (msg) => {
      if (gen !== generation) return
      const data = JSON.parse(msg.data)
      if (data.type === 'event') { lastSeq = data.seq; events.value.push(data); refresh() }
      else if (data.type === 'telemetry') {
        if (data.telemetry && Object.keys(data.telemetry).length) telemetry.value = data.telemetry
        if (mission.value && data.status && data.status !== mission.value.status) refresh()
      } else if (data.type === 'end') refresh()
    }
    socket.onclose = () => {
      if (!unmounted && gen === generation && mission.value?.id === mid && live.value) reconnect = window.setTimeout(() => connect(mid, gen), 1500)
    }
  }

  async function load(mid: string) {
    const gen = ++generation
    closeSocket()
    window.clearTimeout(timer)
    mission.value = null
    events.value = []
    telemetry.value = {}
    lastSeq = 0
    score.value = null
    error.value = ''
    try {
      const m = await getMission(mid)
      if (gen !== generation) return
      mission.value = m
    } catch (e) {
      if (gen === generation) error.value = (e as Error).message
      return
    }
    connect(mid, gen)
    await loadScore(gen)
  }

  watch(id, (v) => { if (v) load(v) }, { immediate: true })
  onBeforeUnmount(() => { unmounted = true; generation++; closeSocket(); window.clearTimeout(timer) })
  const reload = () => { if (id.value) load(id.value) }
  return { mission, events, telemetry, score, error, live, isTroubleshoot, refresh, reload }
}
