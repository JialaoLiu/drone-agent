<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getEval, listEvals } from '../api'

const evals = ref<{ name: string; suite: string; summary: any; scenes?: string[]; repeat?: number; model?: string }[]>([])
const detail = ref<Record<string, any> | null>(null)
const SUITES: Record<string, string> = { vision: '巡检（复核策略对比）', planning: '任务理解与规划', troubleshoot: '飞行排障', layouts: '随机布局泛化', routes: '绕行方法对比' }
const ROUTERS: Record<string, string> = { grid: '栅格 A*', visibility: '可见性图' }
const STRATEGY: Record<string, string> = { none: '只初筛', flagged: '复核疑似缺陷', flagged_and_unsure: '复核疑似与看不清', all: '全部近距复核' }
const latest = computed(() => Object.keys(SUITES).map((k) => evals.value.find((e) => e.suite === k)).filter(Boolean) as typeof evals.value)
const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 1000) / 10}%`)
const stamp = (name: string) => name.replace(/^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2}).*/, '$1-$2-$3 $4:$5')

async function open(name: string) { detail.value = await getEval(name) }
onMounted(async () => { evals.value = await listEvals() })
</script>

<template>
  <div class="page-head"><p>评测脚本的结果（backend/eval/results）。命令见“使用说明”。每次改动后重跑，用数字确认没有退步。</p></div>
  <div class="grid2">
    <section v-for="e in latest" :key="e.name" class="card">
      <div class="head"><h2>{{ SUITES[e.suite] }}</h2><span class="muted small">{{ stamp(e.name) }}</span></div>
      <table v-if="e.suite === 'vision'" class="data">
        <thead><tr><th>策略</th><th class="num">检出率</th><th class="num">精确率</th><th class="num">轻微缺陷</th><th class="num">飞行</th></tr></thead>
        <tbody><tr v-for="(v, k) in e.summary" :key="k"><td>{{ STRATEGY[k] ?? k }}</td><td class="num">{{ pct(v.recall) }}</td><td class="num">{{ pct(v.precision) }}</td><td class="num">{{ pct(v.minor_recall) }}</td><td class="num">{{ v.est_minutes }} 分钟</td></tr></tbody>
      </table>
      <p v-else-if="e.suite === 'planning'">各轮通过率 <b>{{ e.summary.pass_rates.map(pct).join(' / ') }}</b>；不稳定的题 {{ e.summary.unstable.length }}，每轮都失败 {{ e.summary.always_failed.length }}</p>
      <p v-else-if="e.suite === 'troubleshoot'">诊断准确率 <b>{{ pct(e.summary.accuracy) }}</b>（规则基线 {{ pct(e.summary.rule_baseline_accuracy) }}），平均试飞 {{ e.summary.avg_flights }} 次</p>
      <table v-else-if="e.suite === 'layouts'" class="data">
        <thead><tr><th>策略</th><th class="num">通过安全校验</th><th class="num">1 / 2 / 3 架次</th></tr></thead>
        <tbody><tr v-for="k in ['none', 'flagged_and_unsure', 'all']" :key="k"><td>{{ STRATEGY[k] }}</td><td class="num">{{ pct(e.summary[k].pass_rate) }}</td><td class="num">{{ e.summary[k].sorties[1] }} / {{ e.summary[k].sorties[2] }} / {{ e.summary[k].sorties[3] }}</td></tr></tbody>
      </table>
      <table v-else-if="e.suite === 'routes'" class="data">
        <thead><tr><th>方法</th><th class="num">通过</th><th class="num">比直线多</th><th class="num">平均拐点</th><th class="num">平均耗时</th></tr></thead>
        <tbody><tr v-for="(label, k) in ROUTERS" :key="k"><td>{{ label }}</td><td class="num">{{ pct(e.summary[k].plan_pass_rate) }}</td><td class="num">{{ e.summary[k].mean_extra_pct }}%</td>
          <td class="num">{{ e.summary[k].mean_waypoints }}</td><td class="num">{{ e.summary[k].mean_ms }} ms</td></tr></tbody>
      </table>
      <p v-if="e.suite === 'routes'" class="muted small">需要绕行的航段 {{ e.summary.segments }} 段；可见性图总长少 {{ e.summary.length_saving_pct }}%，默认仍用栅格 A*（拐点更少）。</p>
      <button class="small" @click="open(e.name)">查看明细</button>
    </section>
  </div>

  <section class="card gap">
    <h2>全部评测记录</h2>
    <table class="data">
      <thead><tr><th>时间</th><th>评测</th><th>说明</th><th></th></tr></thead>
      <tbody>
        <tr v-for="e in evals" :key="e.name">
          <td class="nowrap">{{ stamp(e.name) }}</td><td>{{ SUITES[e.suite] ?? e.suite }}</td>
          <td class="muted small">{{ e.scenes ? `场景 ${e.scenes.join('、')}，${e.repeat} 轮` : e.repeat ? `${e.repeat} 轮` : '' }}</td>
          <td><button class="small" @click="open(e.name)">明细</button></td>
        </tr>
      </tbody>
    </table>
  </section>

  <section v-if="detail" class="card gap">
    <div class="head"><h2>明细：{{ SUITES[detail.suite] ?? detail.suite }}</h2><button class="small" @click="detail = null">关闭</button></div>
    <template v-if="detail.suite === 'vision'">
      <table class="data">
        <thead><tr><th>轮</th><th>场景</th><th>策略</th><th class="num">检出率</th><th class="num">精确率</th><th>漏检</th><th>误报</th></tr></thead>
        <tbody>
          <template v-for="r in detail.runs" :key="r.round + r.scene">
            <tr v-for="(v, k) in r.strategies" :key="k"><td>{{ r.round }}</td><td>{{ r.scene }}</td><td>{{ STRATEGY[k] }}</td><td class="num">{{ pct(v.recall) }}</td><td class="num">{{ pct(v.precision) }}</td>
              <td class="small">{{ v.missed.join('、') || '—' }}</td><td class="small">{{ v.false_alarms.join('、') || '—' }}</td></tr>
          </template>
        </tbody>
      </table>
    </template>
    <template v-else-if="detail.suite === 'planning'">
      <table class="data">
        <thead><tr><th>轮</th><th>任务</th><th>应为</th><th>实际</th><th>结果</th></tr></thead>
        <tbody><template v-for="(rs, i) in detail.runs" :key="i"><tr v-for="x in rs" :key="x.task"><td>{{ i + 1 }}</td><td>{{ x.task }}</td><td>{{ x.expect }}</td><td>{{ x.got }}{{ x.policy ? ` · ${STRATEGY[x.policy]}` : '' }}{{ x.sorties ? ` · ${x.sorties.length} 架次` : '' }}</td>
          <td><span class="tag" :class="x.passed ? 'ok' : 'bad'">{{ x.passed ? '通过' : x.reason }}</span></td></tr></template></tbody>
      </table>
    </template>
    <template v-else-if="detail.suite === 'troubleshoot'">
      <table class="data">
        <thead><tr><th>场景</th><th>结论</th><th>试飞</th><th>规则基线</th></tr></thead>
        <tbody><tr v-for="r in detail.rows" :key="r.fault"><td>{{ r.label }}</td><td><span class="tag" :class="r.correct ? 'ok' : 'bad'">{{ r.correct ? '正确' : '错误' }}</span> {{ r.root_cause }}</td><td>{{ r.flights }}</td><td>{{ r.rule_baseline_param }}（{{ r.rule_baseline_correct ? '正确' : '错误' }}）</td></tr></tbody>
      </table>
    </template>
    <template v-else-if="detail.suite === 'routes'">
      <table class="data">
        <thead><tr><th>种子</th><th class="num">直线距离</th><th class="num">栅格 A* 长度</th><th class="num">拐点</th><th class="num">可见性图长度</th><th class="num">拐点</th><th>结果</th></tr></thead>
        <tbody><tr v-for="(r, i) in detail.rows" :key="i"><td>{{ r.seed }}</td><td class="num">{{ r.straight }}</td><td class="num">{{ r.grid.length }}</td><td class="num">{{ r.grid.waypoints }}</td>
          <td class="num">{{ r.visibility.length }}</td><td class="num">{{ r.visibility.waypoints }}</td>
          <td><span class="tag" :class="r.grid.ok && r.visibility.ok ? 'ok' : 'bad'">{{ r.grid.ok && r.visibility.ok ? '都通过' : '有未通过' }}</span></td></tr></tbody>
      </table>
    </template>
    <template v-else-if="detail.suite === 'layouts'">
      <table class="data">
        <thead><tr><th>种子</th><th class="num">列</th><th class="num">板</th><th class="num">禁飞区</th><th>只初筛</th><th>复核可疑</th><th>全部复核</th></tr></thead>
        <tbody><tr v-for="r in detail.rows" :key="r.seed"><td>{{ r.seed }}</td><td class="num">{{ r.arrays }}</td><td class="num">{{ r.panels }}</td><td class="num">{{ r.no_fly_zones }}</td>
          <td v-for="k in ['none', 'flagged_and_unsure', 'all']" :key="k"><span class="tag" :class="r[k].ok ? 'ok' : 'bad'">{{ r[k].ok ? '通过' : r[k].violations.join('、') }}</span> {{ r[k].sorties }} 架次</td></tr></tbody>
      </table>
    </template>
  </section>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; }
.gap { margin-top: 14px; }
.nowrap { white-space: nowrap; }
section .small { margin-top: 8px; }
</style>
