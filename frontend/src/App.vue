<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  approveMission, cancelMission, createMission, createScene, emergencyReturn, eventsUrl, getMission, getSim, getSite, getTruth,
  listFaults, listMissions, listScenes, reportUrl, startSim, startTroubleshoot, stopSim, STATUS_LABELS,
} from './api'
import DiagnosisPanel from './components/DiagnosisPanel.vue'
import FindingsPanel from './components/FindingsPanel.vue'
import PhotoStrip from './components/PhotoStrip.vue'
import PlanCard from './components/PlanCard.vue'
import SiteMap from './components/SiteMap.vue'
import Timeline from './components/Timeline.vue'
import type { Mission, MissionEvent, MissionSummary, SceneInfo, SimStatus, Site, Telemetry, Truth } from './types'

const EXAMPLES = [
  '检查 A 和 C 两列光伏板有没有破损或污损',
  '全面仔细检查所有光伏板，一块都不要漏，重点看裂纹',
  '快速扫一遍 B 列，看看有没有明显的问题',
  '电量保留 40%，检查 D 列',
]

const sim = ref<SimStatus>({ scene: null, camera: false, px4: false, telemetry: {}, active_mission: null })
const scenes = ref<SceneInfo[]>([])
const sceneChoice = ref('solar_1')
const site = ref<Site | null>(null)
const truth = ref<Truth | null>(null)
const showTruth = ref(false)
const missions = ref<MissionSummary[]>([])
const mission = ref<Mission | null>(null)
const events = ref<MissionEvent[]>([])
const telemetry = ref<Partial<Telemetry>>({})
const score = ref<Record<string, unknown> | null>(null)
const task = ref('')
const autoApprove = ref(false)
const busy = ref(false)
const starting = ref(false)
const notice = ref('')
const selectedPanel = ref<string | null>(null)
const openPhoto = ref<number | null>(null)
const sidebar = ref(false)
const mode = ref<'inspect' | 'troubleshoot'>('inspect')
const faults = ref<{ id: string; label: string; symptom: string }[]>([])
const fault = ref<string>('')
const symptom = ref('')
const isTroubleshoot = computed(() => (mission.value?.brief as { type?: string } | null)?.type === 'troubleshoot')

let socket: WebSocket | undefined
let lastSeq = 0
let refreshTimer: number | undefined
let pollTimer: number | undefined

const live = computed(() => !!mission.value && ['planning', 'awaiting_approval', 'running'].includes(mission.value.status))
const ready = computed(() => !!sim.value.scene && sim.value.px4 && sim.value.camera)
const battery = computed(() => telemetry.value.battery ?? sim.value.telemetry.battery)

async function pollSim() {
  try {
    sim.value = await getSim()
    if (!live.value) telemetry.value = sim.value.telemetry
    const name = mission.value?.scene ?? sim.value.scene
    if (name && site.value?.name !== name) await loadSite(name)
  } catch { /* 后端未启动时保持上次状态 */ }
}

async function loadSite(name: string) {
  site.value = await getSite(name)
  truth.value = null
  if (showTruth.value) truth.value = await getTruth(name)
}

watch(showTruth, async (on) => {
  truth.value = on && site.value ? await getTruth(site.value.name) : null
})

async function refreshLists() {
  try {
    missions.value = await listMissions()
    scenes.value = await listScenes()
  } catch { /* 忽略 */ }
}

async function start() {
  starting.value = true
  notice.value = ''
  try {
    if (!scenes.value.some((s) => s.name === sceneChoice.value)) {
      const seed = Number(sceneChoice.value.replace('solar_', ''))
      await createScene(seed)
    }
    sim.value = await startSim(sceneChoice.value)
    await loadSite(sceneChoice.value)
  } catch (e) {
    notice.value = `仿真启动失败：${(e as Error).message}`
  } finally {
    starting.value = false
    await refreshLists()
  }
}

async function stop() {
  try { sim.value = await stopSim() } catch (e) { notice.value = (e as Error).message }
}

async function returnHome() {
  try { notice.value = (await emergencyReturn()).message } catch (e) { notice.value = (e as Error).message }
}

async function submit(text = task.value) {
  if (!text.trim() || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const { id } = await createMission(text.trim(), autoApprove.value)
    task.value = ''
    await refreshLists()
    await select(id)
  } catch (e) {
    notice.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function submitTroubleshoot() {
  if (!symptom.value.trim() || busy.value) return
  busy.value = true
  notice.value = ''
  try {
    const { id } = await startTroubleshoot(symptom.value.trim(), fault.value || null)
    symptom.value = ''
    await refreshLists()
    await select(id)
  } catch (e) {
    notice.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

function pickFault() {
  const f = faults.value.find((x) => x.id === fault.value)
  if (f) symptom.value = f.symptom
}

async function select(id: string) {
  sidebar.value = false
  socket?.close()
  events.value = []
  lastSeq = 0
  score.value = null
  selectedPanel.value = null
  mission.value = await getMission(id)
  if (site.value?.name !== mission.value.scene) await loadSite(mission.value.scene)
  connect(id)
  await loadScore()
}

function connect(id: string) {
  socket = new WebSocket(eventsUrl(id, lastSeq))
  socket.onmessage = (m) => {
    const data = JSON.parse(m.data)
    if (data.type === 'event') {
      lastSeq = data.seq
      events.value.push(data)
      scheduleRefresh()
    } else if (data.type === 'telemetry') {
      if (data.telemetry && Object.keys(data.telemetry).length) telemetry.value = data.telemetry
      if (mission.value && data.status && data.status !== mission.value.status) scheduleRefresh()
    } else if (data.type === 'end') {
      scheduleRefresh()
    }
  }
  socket.onclose = () => {
    if (mission.value?.id === id && live.value) setTimeout(() => connect(id), 1500)
  }
}

function scheduleRefresh() {
  window.clearTimeout(refreshTimer)
  refreshTimer = window.setTimeout(async () => {
    if (!mission.value) return
    mission.value = await getMission(mission.value.id)
    if (!live.value) {
      await loadScore()
      await refreshLists()
    }
  }, 400)
}

async function loadScore() {
  if (!mission.value?.result) return
  try {
    const r = await fetch(`/api/missions/${mission.value.id}/score`)
    score.value = r.ok ? await r.json() : null
  } catch { score.value = null }
}

async function decide(approved: boolean) {
  if (!mission.value) return
  busy.value = true
  try { await approveMission(mission.value.id, approved) } catch (e) { notice.value = (e as Error).message } finally { busy.value = false }
  scheduleRefresh()
}

async function cancel() {
  if (!mission.value || !window.confirm('取消任务？飞行中会立即返航。')) return
  await cancelMission(mission.value.id)
  scheduleRefresh()
}

function newMission() {
  socket?.close()
  mission.value = null
  events.value = []
  score.value = null
  sidebar.value = false
}

const time = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
const result = computed(() => (mission.value?.result ?? null) as Record<string, any> | null)

onMounted(async () => {
  await Promise.all([pollSim(), refreshLists()])
  try { faults.value = await listFaults() } catch { faults.value = [] }
  if (sim.value.scene) sceneChoice.value = sim.value.scene
  // 打开页面时接上正在进行（或等待确认）的任务
  const pending = sim.value.active_mission ?? missions.value.find((m) => ['planning', 'awaiting_approval', 'running'].includes(m.status))?.id
  if (pending) await select(pending)
  pollTimer = window.setInterval(pollSim, 2000)
})
onBeforeUnmount(() => { socket?.close(); window.clearInterval(pollTimer) })
</script>

<template>
  <div class="layout">
    <aside :class="{ open: sidebar }">
      <h1>无人机巡检</h1>
      <div class="simbox">
        <label class="small muted" for="scene">仿真场景</label>
        <div class="row">
          <select id="scene" v-model="sceneChoice">
            <option v-for="s in scenes" :key="s.name" :value="s.name">{{ s.name }}（种子 {{ s.seed }}）</option>
            <option v-for="n in [4, 5, 6]" v-show="!scenes.some((s) => s.seed === n)" :key="n" :value="`solar_${n}`">solar_{{ n }}（新生成）</option>
          </select>
        </div>
        <div class="row">
          <button :disabled="starting || !!sim.active_mission" @click="start">{{ starting ? '启动中…' : sim.scene === sceneChoice ? '重启仿真' : '启动仿真' }}</button>
          <button :disabled="!sim.scene || !!sim.active_mission" @click="stop">停止</button>
        </div>
        <p class="small" :class="ready ? 'ok' : 'muted'">
          {{ ready ? `运行中：${sim.scene}` : sim.scene ? `启动中：${sim.scene}（飞控 ${sim.px4 ? '就绪' : '未就绪'}，相机 ${sim.camera ? '就绪' : '未就绪'}）` : '仿真未启动' }}
        </p>
      </div>

      <div class="modes">
        <button :class="{ on: mode === 'inspect' }" @click="mode = 'inspect'">巡检任务</button>
        <button :class="{ on: mode === 'troubleshoot' }" @click="mode = 'troubleshoot'">飞行排障</button>
      </div>
      <div v-if="mode === 'troubleshoot'" class="taskbox">
        <label class="small muted" for="symptom">描述飞行中遇到的问题</label>
        <textarea id="symptom" v-model="symptom" rows="3" placeholder="例如：悬停时机身抖动，拍出来的照片有点糊" />
        <label class="small muted" for="fault">演示：先在仿真中注入一个故障（诊断员不知道是哪个）</label>
        <select id="fault" v-model="fault" @change="pickFault">
          <option value="">不注入，诊断当前飞机</option>
          <option v-for="f in faults" :key="f.id" :value="f.id">{{ f.label }}</option>
        </select>
        <button class="primary wide" :disabled="!symptom.trim() || busy || !ready || !!sim.active_mission" @click="submitTroubleshoot">开始排障</button>
        <p class="small muted">诊断员会先按固定动作试飞一次，再提出假设、在仿真中做对照实验，每次试飞约 1 分钟。</p>
      </div>
      <div v-else class="taskbox">
        <label class="small muted" for="task">巡检任务</label>
        <textarea id="task" v-model="task" rows="3" placeholder="用一句话描述任务，例如：检查 A 和 C 两列有没有破损" @keydown.enter.meta="submit()" />
        <div class="examples">
          <button v-for="e in EXAMPLES" :key="e" class="example" @click="task = e">{{ e }}</button>
        </div>
        <label class="small"><input v-model="autoApprove" type="checkbox" /> 计划通过安全校验后自动起飞（演示用）</label>
        <button class="primary wide" :disabled="!task.trim() || busy || !ready || !!sim.active_mission" @click="submit()">下达任务</button>
        <p v-if="!ready" class="small muted">先启动仿真场景。</p>
      </div>

      <p class="small muted label">历史任务</p>
      <ul class="missions">
        <li v-for="m in missions" :key="m.id" :class="{ on: m.id === mission?.id }" @click="select(m.id)">
          <span class="title">{{ m.task }}</span>
          <small>{{ STATUS_LABELS[m.status] ?? m.status }} · {{ m.scene }} · {{ time(m.created_at) }}</small>
        </li>
        <li v-if="!missions.length" class="muted small">还没有任务</li>
      </ul>
    </aside>

    <main>
      <header>
        <button class="menu" @click="sidebar = !sidebar">菜单</button>
        <div class="chips">
          <span class="chip" :class="(battery ?? 100) < 30 ? 'bad' : ''">电量 {{ battery != null ? battery.toFixed(0) + '%' : '—' }}</span>
          <span class="chip">高度 {{ telemetry.z != null ? telemetry.z.toFixed(1) + ' 米' : '—' }}</span>
          <span class="chip">{{ telemetry.in_air ? '飞行中' : telemetry.armed ? '已解锁' : '地面' }}</span>
          <span class="chip muted">{{ telemetry.flight_mode ?? '—' }}</span>
        </div>
        <button class="danger" :disabled="!telemetry.in_air" @click="returnHome">一键返航</button>
      </header>
      <p v-if="notice" class="notice">{{ notice }}</p>

      <section v-if="mission" class="mission-head card">
        <div>
          <span class="status" :class="mission.status">{{ STATUS_LABELS[mission.status] ?? mission.status }}</span>
          <b>{{ mission.task }}</b>
          <span class="muted small">任务 {{ mission.id }} · {{ mission.scene }} · 模型费用 ¥{{ (mission.usage?.total?.cost_yuan ?? 0).toFixed(4) }}</span>
        </div>
        <div class="row">
          <a v-if="result?.report_path" class="button-link" :href="reportUrl(mission.id)" target="_blank" rel="noopener">查看报告</a>
          <button v-if="live" class="danger" @click="cancel">取消任务</button>
          <button @click="newMission">新任务</button>
        </div>
      </section>

      <div v-if="isTroubleshoot" class="grid">
        <DiagnosisPanel :events="events" />
        <div class="card">
          <h2>诊断过程</h2>
          <Timeline :events="events" />
        </div>
      </div>
      <div v-else class="grid">
        <div class="card map-card">
          <div class="map-head">
            <h2>场地 {{ site?.name ?? '' }}</h2>
            <label class="small"><input v-model="showTruth" type="checkbox" /> 显示标准答案（虚线框）</label>
          </div>
          <SiteMap v-if="site" :site="site" :findings="mission?.findings ?? []" :plan="mission?.plan ?? null"
                   :telemetry="telemetry" :truth="truth" :selected="selectedPanel" @select="selectedPanel = $event" />
          <p v-else class="muted">启动仿真后显示场地平面图。</p>
          <p class="small muted legend">
            <span class="dot" style="background: var(--map-panel)" />光伏板
            <span class="dot" style="background: var(--bad)" />确认缺陷
            <span class="dot" style="background: #e3a008" />疑似
            <span class="dot" style="background: #5b8def" />复核后排除
            <span class="dash" />计划航线（各架次不同颜色）
          </p>
        </div>
        <div class="side">
          <PlanCard v-if="mission?.plan?.sorties" :plan="mission.plan" :awaiting="mission.status === 'awaiting_approval'" :busy="busy" @decide="decide" />
          <div class="card">
            <h2>团队协作记录</h2>
            <Timeline :events="events" />
          </div>
        </div>
      </div>

      <div v-if="result?.summary" class="card summary">
        <h2>报告员结论</h2>
        <p>{{ result.summary }}</p>
      </div>
      <FindingsPanel v-if="mission && !isTroubleshoot" :findings="mission.findings" :selected="selectedPanel" :score="score"
                     @select="selectedPanel = $event" @photo="openPhoto = $event" />
      <PhotoStrip v-if="mission && !isTroubleshoot" :photos="mission.photos" :open="openPhoto" @open="openPhoto = $event" />

      <div v-if="!mission" class="card intro">
        <h2>使用方法</h2>
        <ol>
          <li>在左侧选择场景并启动仿真（首次约 30 秒）。场景里有 4 列共 80 块光伏板，其中约 10 块有缺陷。</li>
          <li>用一句话下达巡检任务。调度员理解任务，规划员给出航线和复核策略，安全员校验围栏、禁飞区、障碍物和电量。</li>
          <li>确认计划后起飞：飞手逐列拍摄，巡检员初筛并对可疑的板用变焦相机近距复核，电量不够时自动分架次换电池。</li>
          <li>完成后查看报告；勾选“显示标准答案”可以对照检出是否准确。</li>
        </ol>
      </div>
    </main>
  </div>
</template>

<style scoped>
.layout { display: flex; min-height: 100vh; }
aside { width: 300px; flex-shrink: 0; border-right: 1px solid var(--line); padding: 16px 14px; background: var(--panel); overflow-y: auto; height: 100vh; position: sticky; top: 0; }
h1 { font-size: 20px; margin: 0 0 14px; }
.simbox, .taskbox { display: flex; flex-direction: column; gap: 8px; padding-bottom: 14px; margin-bottom: 14px; border-bottom: 1px solid var(--line); }
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.row select { flex: 1; min-width: 0; }
textarea { width: 100%; resize: vertical; }
.examples { display: flex; flex-direction: column; gap: 4px; }
.modes { display: flex; gap: 6px; margin-bottom: 10px; }
.modes button { flex: 1; border-radius: 8px; }
.modes button.on { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 10%, var(--panel)); }
.example { text-align: left; font-size: 12px; border-radius: 8px; padding: 4px 8px; color: var(--muted); }
.wide { width: 100%; }
.ok { color: var(--ok); }
.label { margin: 0 0 6px; }
.missions { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 2px; }
.missions li { padding: 8px; border-radius: 8px; cursor: pointer; display: flex; flex-direction: column; gap: 2px; }
.missions li:hover, .missions li.on { background: var(--code); }
.missions .title { font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.missions small { color: var(--muted); font-size: 11px; }
main { flex: 1; min-width: 0; padding: 14px 18px 40px; display: flex; flex-direction: column; gap: 14px; }
header { display: flex; align-items: center; gap: 10px; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 13px; padding: 4px 10px; border-radius: 999px; background: var(--panel); border: 1px solid var(--line); font-variant-numeric: tabular-nums; }
.chip.bad { color: var(--bad); border-color: var(--bad); }
header .danger { margin-left: auto; }
.menu { display: none; }
.notice { margin: 0; padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); font-size: 14px; }
.mission-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }
.mission-head > div:first-child { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.status { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--code); }
.status.running, .status.planning { background: #dbeafe; color: #1e40af; }
.status.awaiting_approval { background: var(--warn-bg); color: var(--warn); }
.status.done { background: var(--ok-bg); color: var(--ok); }
.status.failed, .status.rejected, .status.cancelled { background: var(--bad-bg); color: var(--bad); }
.button-link { border: 1px solid var(--accent); color: var(--accent); border-radius: 999px; padding: 6px 14px; text-decoration: none; font-size: 14px; }
.grid { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); gap: 14px; align-items: start; }
.side { display: flex; flex-direction: column; gap: 14px; }
.map-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.map-head h2 { margin: 0; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: center; margin: 8px 0 0; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 4px; vertical-align: -1px; }
.dash { display: inline-block; width: 18px; border-top: 2px dashed var(--map-route); margin-right: 4px; vertical-align: 3px; }
.summary p { margin: 0; line-height: 1.7; }
.intro ol { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 14px; }
@media (max-width: 980px) {
  .grid { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  aside { position: fixed; inset: 0 auto 0 0; z-index: 10; transform: translateX(-100%); transition: transform 0.2s; }
  aside.open { transform: none; box-shadow: 0 0 24px rgba(0, 0, 0, 0.25); }
  .menu { display: block; }
  main { padding: 12px 16px 40px; }
}
</style>
