<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { approveMission, cancelMission, eventsUrl, getMission, getScore, getSite, getTruth, reportUrl, STATUS_LABELS } from '../api'
import DiagnosisPanel from '../components/DiagnosisPanel.vue'
import FindingCards from '../components/FindingCards.vue'
import PhotoStrip from '../components/PhotoStrip.vue'
import PlanCard from '../components/PlanCard.vue'
import MapView from '../components/MapView.vue'
import Timeline from '../components/Timeline.vue'
import { useSim } from '../composables/sim'
import type { Mission, MissionEvent, Site, Telemetry, Truth } from '../types'

const route = useRoute()
const router = useRouter()
const { sim } = useSim()
const mission = ref<Mission | null>(null)
const events = ref<MissionEvent[]>([])
const site = ref<Site | null>(null)
const truth = ref<Truth | null>(null)
const showTruth = ref(false)
const score = ref<Record<string, any> | null>(null)
const telemetry = ref<Partial<Telemetry>>({})
const selectedPanel = ref<string | null>(null)
const openPhoto = ref<number | null>(null)
const busy = ref(false)
const error = ref('')
let socket: WebSocket | undefined
let lastSeq = 0
let timer: number | undefined
let reconnectTimer: number | undefined
let generation = 0          // 每次切换任务加一；旧任务的异步结果与事件一律丢弃（深度审查 H10）
let unmounted = false

function closeSocket() {
  window.clearTimeout(reconnectTimer)
  if (socket) {
    socket.onclose = null       // 主动关闭不触发重连（深度审查 M02）
    socket.onmessage = null
    socket.close()
    socket = undefined
  }
}

const routeId = () => String(route.params.id ?? '')

const live = computed(() => !!mission.value && ['planning', 'awaiting_approval', 'running'].includes(mission.value.status))
const isTroubleshoot = computed(() => (mission.value?.brief as { type?: string } | null)?.type === 'troubleshoot')
const result = computed(() => (mission.value?.result ?? null) as Record<string, any> | null)
const mapTelemetry = computed(() => (live.value ? telemetry.value : sim.value.scene === mission.value?.scene ? sim.value.telemetry : {}))

async function load(id: string) {
  const gen = ++generation
  closeSocket()
  window.clearTimeout(timer)
  mission.value = null          // 先清空旧任务，加载完成前操作按钮不可用
  events.value = []
  lastSeq = 0
  score.value = null
  error.value = ''
  let loaded: Mission
  try {
    loaded = await getMission(id)
  } catch (e) {
    if (gen === generation) error.value = (e as Error).message
    return
  }
  if (gen !== generation) return
  mission.value = loaded
  if (site.value?.name !== loaded.scene && loaded.scene !== 'eval') {
    let s: Site | null = null
    try { s = await getSite(loaded.scene) } catch { s = null }
    if (gen !== generation) return
    site.value = s
  }
  connect(id, gen)
  await loadScore(gen)
}

function connect(id: string, gen: number) {
  if (unmounted || gen !== generation) return
  socket = new WebSocket(eventsUrl(id, lastSeq))
  socket.onmessage = (m) => {
    if (gen !== generation) return
    const data = JSON.parse(m.data)
    if (data.type === 'event') {
      lastSeq = data.seq
      events.value.push(data)
      refresh()
    } else if (data.type === 'telemetry') {
      if (data.telemetry && Object.keys(data.telemetry).length) telemetry.value = data.telemetry
      if (mission.value && data.status && data.status !== mission.value.status) refresh()
    } else if (data.type === 'end') {
      refresh()
    }
  }
  socket.onclose = () => {
    if (!unmounted && gen === generation && mission.value?.id === id && live.value) {
      reconnectTimer = window.setTimeout(() => connect(id, gen), 1500)
    }
  }
}

function refresh() {
  const gen = generation
  window.clearTimeout(timer)
  timer = window.setTimeout(async () => {
    const id = routeId()
    if (!id || gen !== generation) return
    const m = await getMission(id)
    if (gen !== generation) return
    mission.value = m
    if (!live.value) await loadScore(gen)
  }, 400)
}

async function loadScore(gen = generation) {
  if (!mission.value?.result || isTroubleshoot.value) return
  const id = mission.value.id
  let s: Record<string, any> | null = null
  try { s = await getScore(id) } catch { s = null }
  if (gen === generation) score.value = s
}

watch(showTruth, async (on) => { truth.value = on && site.value ? await getTruth(site.value.name) : null })
watch(() => route.params.id, (id) => { if (id) load(String(id)) }, { immediate: true })
onBeforeUnmount(() => {
  unmounted = true
  generation++
  closeSocket()
  window.clearTimeout(timer)
})

// 操作对象以地址栏中的任务编号为准，并核对页面上显示的任务是同一个
function currentId(): string | null {
  const id = routeId()
  return mission.value && mission.value.id === id ? id : null
}

async function decide(approved: boolean) {
  const id = currentId()
  if (!id) return
  busy.value = true
  try { await approveMission(id, approved) } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
  refresh()
}

async function cancel() {
  const id = currentId()
  if (!id || !window.confirm('取消任务？飞行中会立即返航。')) return
  await cancelMission(id)
  refresh()
}
</script>

<template>
  <p v-if="error" class="notice">{{ error }}</p>
  <template v-if="mission">
    <section class="card head">
      <div class="title">
        <span class="tag" :class="mission.status">{{ STATUS_LABELS[mission.status] ?? mission.status }}</span>
        <b>{{ mission.task }}</b>
        <span class="muted small">{{ isTroubleshoot ? '飞行排障' : '巡检任务' }} · {{ mission.id }} · 场景 {{ mission.scene }} · 模型费用 ¥{{ (mission.usage?.total?.cost_yuan ?? 0).toFixed(4) }}</span>
      </div>
      <div class="row">
        <a v-if="result?.report_path" class="button-link" :href="reportUrl(mission.id)" target="_blank" rel="noopener">查看报告</a>
        <button v-if="live" class="danger" @click="cancel">取消任务</button>
        <button @click="router.push(isTroubleshoot ? '/troubleshoot' : '/missions')">返回列表</button>
      </div>
    </section>

    <div v-if="isTroubleshoot" class="layout">
      <DiagnosisPanel :events="events" />
      <div class="card"><h2>诊断过程</h2><Timeline :events="events" /></div>
    </div>

    <template v-else>
      <div class="layout">
        <div class="card">
          <div class="map-head">
            <h2>场地 {{ site?.name ?? '' }}</h2>
            <label class="small"><input v-model="showTruth" type="checkbox" /> 显示标准答案（虚线框）</label>
          </div>
          <MapView v-if="site" :site="site" :findings="mission.findings" :plan="mission.plan" :telemetry="mapTelemetry"
                   :truth="truth" :selected="selectedPanel" @select="selectedPanel = $event" />
          <p class="small muted legend">
            <span class="dot" style="background: var(--map-panel)" />光伏板
            <span class="dot" style="background: var(--bad)" />确认缺陷
            <span class="dot" style="background: #e3a008" />待确认
            <span class="dot" style="background: #5b8def" />复核后排除
            <span class="dash" />计划航线（各架次不同颜色，空心圆为绕开禁飞区的绕行点）
          </p>
        </div>
        <div class="side">
          <PlanCard v-if="mission.plan?.sorties" :plan="mission.plan" :awaiting="mission.status === 'awaiting_approval'" :busy="busy" @decide="decide" />
          <div class="card"><h2>团队协作记录</h2><Timeline :events="events" /></div>
        </div>
      </div>
      <div v-if="result?.summary" class="card summary"><h2>报告员结论</h2><p>{{ result.summary }}</p></div>
      <FindingCards :findings="mission.findings" :selected="selectedPanel" :score="score" @select="selectedPanel = $event" @photo="openPhoto = $event" />
      <PhotoStrip :photos="mission.photos" :open="openPhoto" @open="openPhoto = $event" />
    </template>
  </template>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 14px; }
.title { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.button-link { border: 1px solid var(--accent); color: var(--accent); border-radius: 999px; padding: 6px 14px; text-decoration: none; font-size: 14px; }
.layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); gap: 14px; align-items: start; margin-bottom: 14px; }
.side { display: flex; flex-direction: column; gap: 14px; }
.map-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.map-head h2 { margin: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; margin: 8px 0 0; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.dash { display: inline-block; width: 18px; border-top: 2px dashed var(--map-route); margin-right: 4px; vertical-align: 3px; }
.summary { margin-bottom: 14px; }
.summary p { margin: 0; line-height: 1.7; }
.card + .card { margin-top: 0; }
:deep(.card) { margin-bottom: 0; }
.notice { padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); }
@media (max-width: 1000px) { .layout { grid-template-columns: 1fr; } }
</style>
