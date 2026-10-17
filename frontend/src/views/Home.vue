<script setup lang="ts">
// 工作台右侧的默认内容：当前场景预览（真实场地显示卫星图）、三步上手、简要统计
import { computed, onMounted, ref, watch } from 'vue'
import { getStats } from '../api'
import MapView from '../components/map/MapView.vue'
import { scenarioOf } from '../scenarios'
import { useSim } from '../stores/sim'
import { useSites } from '../stores/sites'
import type { Site } from '../types'

const { sim, phase, phaseText } = useSim()
const { load } = useSites()
const site = ref<Site | null>(null)
const stats = ref<Record<string, any> | null>(null)
watch(() => sim.value.scene, async (name) => { const s = await load(name); if (name === sim.value.scene) site.value = s }, { immediate: true })
onMounted(async () => { stats.value = await getStats().catch(() => null) })
const scenario = computed(() => scenarioOf(site.value?.scenario))
</script>

<template>
  <div class="home">
    <section class="card steps">
      <h2>三步开始</h2>
      <ol>
        <li :class="{ done: !!sim.scene }"><b>选场景</b><span>在 <router-link to="/scenes">场景库</router-link> 选一个随机场地，或搜索真实地点生成数字孪生场地，然后启动仿真。</span></li>
        <li :class="{ done: phase === 'ready' || phase === 'busy' }"><b>下达任务</b><span>在左侧用一句话描述任务（光伏巡检或应急救援，取决于场景）。调度员理解、规划员出计划、安全员校验，确认后起飞。</span></li>
        <li><b>看结果</b><span>任务进度、实时视角、发现与报告都在这个工作台里；历史任务在左侧随时可以回看。</span></li>
      </ol>
      <p class="small muted">当前：{{ phaseText }}{{ sim.scene ? ` · ${sim.scene}` : '' }}</p>
    </section>

    <section v-if="site" class="card">
      <h2>当前场景 {{ site.name }} <span class="muted small">{{ scenario.label }}{{ site.location ? ' · ' + site.location.name : ' · 随机生成' }}</span></h2>
      <MapView :site="site" :findings="[]" :plan="null" :telemetry="sim.telemetry" />
    </section>

    <section v-if="stats" class="stats">
      <div class="stat"><span>任务</span><b>{{ stats.mission_total }}</b><small>完成 {{ stats.missions?.done ?? 0 }} · 失败 {{ stats.missions?.failed ?? 0 }}</small></div>
      <div class="stat"><span>确认的发现</span><b>{{ stats.defect_total }}</b><small>均经近距复核或一眼可见</small></div>
      <div class="stat"><span>照片</span><b>{{ stats.photos }}</b><small>整列、分段与近照</small></div>
      <div class="stat"><span>任务模型费用</span><b>¥{{ stats.cost_yuan?.toFixed(2) }}</b><small><router-link to="/usage">含评测的明细</router-link></small></div>
    </section>
  </div>
</template>

<style scoped>
.home { display: flex; flex-direction: column; gap: 14px; max-width: 1100px; }
.steps ol { margin: 0; padding: 0; list-style: none; display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; counter-reset: s; }
.steps li { border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; counter-increment: s; }
.steps li b::before { content: counter(s) '. '; }
.steps li.done { border-color: var(--ok); }
.steps li span { font-size: 13px; color: var(--muted); line-height: 1.5; }
</style>
