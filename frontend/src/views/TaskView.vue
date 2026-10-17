<script setup lang="ts">
// 任务视图（工作台右侧）：巡检任务与飞控诊断共用。只负责组合展示组件，数据来自 useTaskStream。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { approveMission, getTruth, reportUrl, STATUS_LABELS } from '../api'
import LiveView from '../components/task/LiveView.vue'
import DiagnosisPanel from '../components/task/DiagnosisPanel.vue'
import FindingCards from '../components/task/FindingCards.vue'
import PhotoStrip from '../components/task/PhotoStrip.vue'
import PlanCard from '../components/task/PlanCard.vue'
import TaskProgress from '../components/task/TaskProgress.vue'
import TaskVerdict from '../components/task/TaskVerdict.vue'
import Timeline from '../components/task/Timeline.vue'
import TruthComparison from '../components/task/TruthComparison.vue'
import RescueResult from '../components/task/RescueResult.vue'
import ClarifyReply from '../components/task/ClarifyReply.vue'
import MapView from '../components/map/MapView.vue'
import Tabs from '../components/common/Tabs.vue'
import { DIAGNOSIS_STAGES, scenarioOf } from '../scenarios'
import { useMissions } from '../stores/missions'
import { useSim } from '../stores/sim'
import { useSites } from '../stores/sites'
import { useTaskStream } from '../stores/taskStream'
import { useToast } from '../stores/toast'
import type { Marker, Site, Truth } from '../types'

const route = useRoute()
const router = useRouter()
const id = computed(() => (route.params.id ? String(route.params.id) : null))
const { mission, events, telemetry, score, error, live, isTroubleshoot, refresh, reload } = useTaskStream(id)
const { sim } = useSim()
const { cancel } = useMissions()
const { load } = useSites()
const { show } = useToast()
const site = ref<Site | null>(null)
const truth = ref<Truth | null>(null)
const showTruth = ref(false)
const selected = ref<string | null>(null)
const openPhoto = ref<number | null>(null)
const busy = ref(false)

// 场地与标准答案的请求按场景名核对后再写入：快速切换任务时，旧任务的慢请求不能覆盖新任务（审查二 M01）
watch(() => mission.value?.scene, async (name) => {
  site.value = null
  truth.value = null
  const s = await load(name)
  if (name === mission.value?.scene) site.value = s
}, { immediate: true })
// 评分、场地、显示开关任一变化都重新判断是否需要答案：评分先于场地返回时也能补上（审查二 M02）
watch([score, showTruth, () => site.value?.name], async () => {
  const name = site.value?.name
  if (!name || truth.value || !(score.value || showTruth.value)) return
  const t = await getTruth(name).catch(() => null)
  if (name === site.value?.name) truth.value = t
})
const scenario = computed(() => scenarioOf(site.value?.scenario))
const result = computed(() => (mission.value?.result ?? null) as Record<string, any> | null)
const flying = computed(() => live.value && mission.value?.status === 'running' && sim.value.scene === mission.value?.scene)
const mapTelemetry = computed(() => (live.value ? telemetry.value : {}))
// 救援：把搜索中看到的人和物画在地图上（目标红色、疑似与排除橙色、路人与物体灰色）
const markers = computed<Marker[]>(() => ((result.value?.detections ?? []) as any[]).map((d) => ({
  x: d.x, y: d.y, label: `${d.id} ${scenario.value.findingTypes[d.kind] ?? ''}`,
  tone: d.kind === 'target' ? 'target' : ['candidate', 'rejected'].includes(d.kind) ? 'warn' : 'muted',
})))
const unverified = computed(() => mission.value?.findings.filter((f) => f.status === 'unverified' || f.status === 'suspect').length ?? 0)

async function decide(approved: boolean) {
  if (!mission.value || mission.value.id !== id.value) return
  busy.value = true
  try { await approveMission(mission.value.id, approved); show(approved ? '已确认，准备起飞' : '已取消这个计划', approved ? 'ok' : 'warn') }
  catch (e) { show((e as Error).message, 'bad') } finally { busy.value = false }
  refresh()
}
async function doCancel() {
  if (!mission.value || !window.confirm('取消任务？飞行中会立即返航。')) return
  await cancel(mission.value.id)
  refresh()
}
function select(pid: string) {
  selected.value = pid
  pick('map')
}

// 页签（第三轮反馈：一屏堆叠太多）：默认跟随任务状态——待确认看概览、飞行中看地图与画面、结束后看结果；
// 用户手动点过页签后不再自动切换，换任务时恢复自动
const tab = ref('overview')
const manual = ref(false)
const autoTab = computed(() => {
  const st = mission.value?.status
  if (st === 'running') return 'map'
  if (['done', 'failed', 'cancelled'].includes(st ?? '') && result.value) return 'result'
  return 'overview'
})
watch(autoTab, (t) => { if (!manual.value) tab.value = t }, { immediate: true })
watch(id, () => { manual.value = false; tab.value = autoTab.value })
function pick(t: string) { tab.value = t; manual.value = true }
const tabs = computed(() => {
  const r = result.value
  const found = scenario.value.usesPanels ? mission.value?.findings.length : (r?.persons?.length ?? r?.detections?.filter((d: any) => ['target', 'person', 'water'].includes(d.kind)).length)
  return [
    { key: 'overview', label: '概览' },
    { key: 'map', label: flying.value ? '地图与实时画面' : '地图' },
    { key: 'process', label: '过程', badge: events.value.length || null },
    { key: 'result', label: '结果', badge: found || null },
  ]
})
</script>

<template>
  <p v-if="error" class="notice">{{ error }}</p>
  <div v-if="!mission && !error" class="muted small">加载中……</div>
  <template v-if="mission">
    <section class="head">
      <div class="title">
        <b>{{ mission.task }}</b>
        <div class="meta">
          <span class="tag" :class="mission.status">{{ STATUS_LABELS[mission.status] ?? mission.status }}</span>
          <span class="muted small">{{ isTroubleshoot ? '飞控诊断' : scenario.label }} · 场景 {{ mission.scene }} · 编号 {{ mission.id }} · 模型费用 ¥{{ (mission.usage?.total?.cost_yuan ?? 0).toFixed(4) }}</span>
        </div>
      </div>
      <div class="actions">
        <a v-if="result?.report_path" class="button-link" :href="reportUrl(mission.id)" target="_blank" rel="noopener">查看报告</a>
        <button v-if="live" class="danger" @click="doCancel">取消任务</button>
        <button class="back" @click="router.push('/')">返回</button>
      </div>
    </section>

    <ClarifyReply v-if="mission.status === 'clarify'" :mission-id="mission.id" :events="events" @sent="reload" />
    <TaskProgress :events="events" :status="mission.status" :stages="isTroubleshoot ? DIAGNOSIS_STAGES : scenario.stages" />

    <div v-if="isTroubleshoot" class="layout">
      <DiagnosisPanel :events="events" />
      <div class="card"><h2>诊断过程</h2><Timeline :events="events" /></div>
    </div>

    <template v-else>
      <TaskVerdict :score="score" :result="result" :unverified="unverified" :status="mission.status" :scenario="scenario" />
      <Tabs :items="tabs" :model-value="tab" @update:model-value="pick" />

      <div v-show="tab === 'overview'" class="pane overview">
        <PlanCard v-if="mission.plan?.sorties" :plan="mission.plan" :awaiting="mission.status === 'awaiting_approval'" :busy="busy" :scenario="scenario" @decide="decide" />
        <div v-if="result?.summary" class="card summary"><h2>报告员结论</h2><p>{{ result.summary }}</p></div>
        <div v-if="!mission.plan?.sorties && !result?.summary" class="card muted small">调度员正在理解任务，计划出来后会显示在这里。</div>
      </div>

      <div v-show="tab === 'map'" class="pane" :class="{ split: flying }">
        <LiveView :active="flying && tab === 'map'" />
        <div class="card map-card">
          <div class="map-head">
            <h2>场地 {{ site?.name ?? '' }}</h2>
            <label v-if="!score || !scenario.usesPanels" class="small"><input v-model="showTruth" type="checkbox" /> 显示标准答案</label>
          </div>
          <MapView v-if="site" :site="site" :findings="mission.findings" :plan="mission.plan" :telemetry="mapTelemetry"
                   :truth="showTruth || (score && scenario.usesPanels) ? truth : null" :score="scenario.usesPanels ? score : null" :selected="selected"
                   :markers="markers" @select="selected = $event" />
        </div>
      </div>

      <div v-show="tab === 'process'" class="pane">
        <div class="card"><h2>团队协作记录</h2><Timeline :events="events" /></div>
      </div>

      <div v-show="tab === 'result'" class="pane">
        <TruthComparison v-if="scenario.usesPanels && score && score.defects != null" :score="score" :truth="truth" :scenario="scenario" @select="select" />
        <RescueResult v-if="!scenario.usesPanels && result" :result="result" :scenario="scenario" @photo="openPhoto = $event" />
        <FindingCards v-if="scenario.usesPanels" :findings="mission.findings" :selected="selected" :types="scenario.findingTypes" @select="select" @photo="openPhoto = $event" />
        <PhotoStrip :photos="mission.photos" :open="openPhoto" @open="openPhoto = $event" />
        <p v-if="!result && !mission.photos.length" class="muted small">任务结束后在这里查看发现、照片与标准答案对照。</p>
      </div>
    </template>
  </template>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; }
.title { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
.title b { font-size: 18px; font-weight: 600; line-height: 1.4; }
.meta { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.button-link { border: 1px solid var(--line); color: var(--text); border-radius: var(--radius-sm); padding: 7px 14px; text-decoration: none; font-size: 14px; background: var(--panel); }
.button-link:hover { border-color: var(--accent); color: var(--accent); }
.pane { display: flex; flex-direction: column; gap: 16px; }
.pane :deep(.card) { margin-bottom: 0; }
.pane.split { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; }
.overview { max-width: 860px; }
.layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); gap: 16px; align-items: start; margin-bottom: 16px; }
.map-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.map-head h2 { margin: 0; }
.summary p { margin: 0; line-height: 1.8; }
.notice { padding: 10px 14px; border-radius: var(--radius-sm); background: var(--warn-bg); color: var(--warn); }
.back { display: none; }
@media (max-width: 1200px) { .pane.split { grid-template-columns: 1fr; } }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
@media (max-width: 960px) { .back { display: inline-block; } }
</style>
