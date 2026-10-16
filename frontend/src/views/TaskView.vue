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
import MapView from '../components/map/MapView.vue'
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
const { mission, events, telemetry, score, error, live, isTroubleshoot, refresh } = useTaskStream(id)
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

watch(() => mission.value?.scene, async (name) => { site.value = await load(name); truth.value = null })
watch([score, showTruth], async () => {
  if ((score.value || showTruth.value) && site.value && !truth.value) truth.value = await getTruth(site.value.name).catch(() => null)
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
  document.querySelector('.map-card')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
</script>

<template>
  <p v-if="error" class="notice">{{ error }}</p>
  <div v-if="!mission && !error" class="muted small">加载中……</div>
  <template v-if="mission">
    <section class="head">
      <div class="title">
        <span class="tag" :class="mission.status">{{ STATUS_LABELS[mission.status] ?? mission.status }}</span>
        <b>{{ mission.task }}</b>
        <span class="muted small">{{ isTroubleshoot ? '飞控诊断' : scenario.label }} · {{ mission.id }} · 场景 {{ mission.scene }} · 模型费用 ¥{{ (mission.usage?.total?.cost_yuan ?? 0).toFixed(4) }}</span>
      </div>
      <div class="actions">
        <a v-if="result?.report_path" class="button-link" :href="reportUrl(mission.id)" target="_blank" rel="noopener">查看报告</a>
        <button v-if="live" class="danger" @click="doCancel">取消任务</button>
        <button class="back" @click="router.push('/')">返回</button>
      </div>
    </section>

    <TaskProgress :events="events" :status="mission.status" :stages="isTroubleshoot ? DIAGNOSIS_STAGES : scenario.stages" />

    <div v-if="isTroubleshoot" class="layout">
      <DiagnosisPanel :events="events" />
      <div class="card"><h2>诊断过程</h2><Timeline :events="events" /></div>
    </div>

    <template v-else>
      <TaskVerdict :score="score" :result="result" :unverified="unverified" :status="mission.status" :scenario="scenario" />
      <div class="layout">
        <div class="col">
          <LiveView :active="flying" />
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
        <div class="col">
          <PlanCard v-if="mission.plan?.sorties" :plan="mission.plan" :awaiting="mission.status === 'awaiting_approval'" :busy="busy" @decide="decide" />
          <TruthComparison v-if="scenario.usesPanels && score && score.defects != null" :score="score" :truth="truth" :scenario="scenario" @select="select" />
          <RescueResult v-if="!scenario.usesPanels && result" :result="result" :scenario="scenario" @photo="openPhoto = $event" />
          <div class="card"><h2>团队协作记录</h2><Timeline :events="events" /></div>
        </div>
      </div>
      <div v-if="result?.summary" class="card summary"><h2>报告员结论</h2><p>{{ result.summary }}</p></div>
      <FindingCards v-if="scenario.usesPanels" :findings="mission.findings" :selected="selected" :types="scenario.findingTypes" @select="selected = $event" @photo="openPhoto = $event" />
      <PhotoStrip :photos="mission.photos" :open="openPhoto" @open="openPhoto = $event" />
    </template>
  </template>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; margin-bottom: 12px; }
.title { display: flex; gap: 10px; align-items: baseline; flex-wrap: wrap; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.button-link { border: 1px solid var(--accent); color: var(--accent); border-radius: 999px; padding: 6px 14px; text-decoration: none; font-size: 14px; }
.layout { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(300px, 1fr); gap: 14px; align-items: start; margin-bottom: 14px; }
.col { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.col :deep(.card) { margin-bottom: 0; }
.map-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.map-head h2 { margin: 0; }
.summary { margin-bottom: 14px; }
.summary p { margin: 0; line-height: 1.7; }
.notice { padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); }
.back { display: none; }
@media (max-width: 1100px) { .layout { grid-template-columns: 1fr; } }
@media (max-width: 960px) { .back { display: inline-block; } }
</style>
