<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { DEFECT_LABELS, getStats, listEvals, listMissions, STATUS_LABELS } from '../api'
import BarChart from '../components/BarChart.vue'
import { useSim } from '../composables/sim'
import type { MissionSummary } from '../types'

const router = useRouter()
const { sim, ready } = useSim()
const stats = ref<Record<string, any> | null>(null)
const missions = ref<MissionSummary[]>([])
const evals = ref<{ name: string; suite: string; summary: any }[]>([])

const latest = (suite: string) => evals.value.find((e) => e.suite === suite)?.summary
const vision = computed(() => latest('vision'))
const planning = computed(() => latest('planning'))
const trouble = computed(() => latest('troubleshoot'))
const layouts = computed(() => latest('layouts'))
const defectBars = computed(() => Object.entries(stats.value?.defects ?? {}).map(([k, v]) => ({ label: DEFECT_LABELS[k] ?? k, value: v as number, color: 'var(--bad)' })))
const pct = (v: number | undefined | null) => (v == null ? '—' : `${Math.round(v * 1000) / 10}%`)
const time = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

onMounted(async () => {
  const [s, m, e] = await Promise.allSettled([getStats(), listMissions(), listEvals()])
  if (s.status === 'fulfilled') stats.value = s.value
  if (m.status === 'fulfilled') missions.value = m.value.slice(0, 6)
  if (e.status === 'fulfilled') evals.value = e.value
})
</script>

<template>
  <div class="stats">
    <div class="stat"><span>任务</span><b>{{ stats?.mission_total ?? '—' }}</b><small>完成 {{ stats?.missions?.done ?? 0 }} · 失败 {{ stats?.missions?.failed ?? 0 }}</small></div>
    <div class="stat"><span>确认缺陷</span><b>{{ stats?.defect_total ?? '—' }}</b><small>均经近距复核或一眼可见</small></div>
    <div class="stat"><span>照片</span><b>{{ stats?.photos ?? '—' }}</b><small>整列 + 近照</small></div>
    <div class="stat"><span>模型费用</span><b>¥{{ stats?.cost_yuan?.toFixed(2) ?? '—' }}</b><small>不含评测，<router-link to="/usage">查看明细</router-link></small></div>
  </div>

  <div class="grid2 gap">
    <section class="card">
      <h2>仿真</h2>
      <p v-if="ready" class="ok">运行中：场景 <b>{{ sim.scene }}</b>，飞控与相机已就绪。</p>
      <p v-else-if="sim.scene" class="muted">场景 {{ sim.scene }} 启动中……</p>
      <p v-else class="muted">仿真未启动。</p>
      <div class="row">
        <button class="primary" :disabled="!ready" @click="router.push('/missions')">下达巡检任务</button>
        <button :disabled="!ready" @click="router.push('/troubleshoot')">飞行排障</button>
        <button @click="router.push('/scenes')">{{ sim.scene ? '更换场景' : '选择场景并启动' }}</button>
      </div>
    </section>
    <section class="card">
      <h2>确认的缺陷类型</h2>
      <BarChart :items="defectBars" unit=" 块" :height="120" />
    </section>
  </div>

  <section class="card gap">
    <div class="head"><h2>最近的任务</h2><router-link to="/missions">全部任务</router-link></div>
    <table class="data">
      <tbody>
        <tr v-for="m in missions" :key="m.id" class="clickable" @click="router.push(`/missions/${m.id}`)">
          <td>{{ m.task }}</td><td class="muted">{{ m.scene }}</td>
          <td><span class="tag" :class="m.status">{{ STATUS_LABELS[m.status] ?? m.status }}</span></td><td class="muted nowrap">{{ time(m.created_at) }}</td>
        </tr>
        <tr v-if="!missions.length"><td class="muted">还没有任务</td></tr>
      </tbody>
    </table>
  </section>

  <section class="card gap">
    <div class="head"><h2>评测结果（最近一次）</h2><router-link to="/evals">全部评测</router-link></div>
    <div class="stats">
      <div class="stat"><span>巡检 · 全部近距复核</span><b>{{ pct(vision?.all?.recall) }}</b><small>检出率；精确率 {{ pct(vision?.all?.precision) }}</small></div>
      <div class="stat"><span>巡检 · 只复核可疑</span><b>{{ pct(vision?.flagged_and_unsure?.precision) }}</b><small>精确率；检出率 {{ pct(vision?.flagged_and_unsure?.recall) }}</small></div>
      <div class="stat"><span>任务理解与规划</span><b>{{ planning ? planning.pass_rates.map((r: number) => pct(r)).join(' / ') : '—' }}</b><small>各轮通过率</small></div>
      <div class="stat"><span>飞行排障</span><b>{{ pct(trouble?.accuracy) }}</b><small>规则基线 {{ pct(trouble?.rule_baseline_accuracy) }}</small></div>
      <div class="stat"><span>随机布局</span><b>{{ pct(layouts?.all?.pass_rate) }}</b><small>通过安全校验（全部复核）</small></div>
    </div>
  </section>
</template>

<style scoped>
.gap { margin-top: 14px; }
.row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
.ok { color: var(--ok); }
.head { display: flex; justify-content: space-between; align-items: center; }
.clickable { cursor: pointer; }
.clickable:hover { background: var(--code); }
.nowrap { white-space: nowrap; }
</style>
