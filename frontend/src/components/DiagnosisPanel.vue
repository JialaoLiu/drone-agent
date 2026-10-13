<script setup lang="ts">
import { computed } from 'vue'
import type { MissionEvent } from '../types'

const props = defineProps<{ events: MissionEvent[] }>()

interface MetricRow { value: number | null; unit: string; threshold: number | null; status: string }
const LABELS: Record<string, string> = {
  attitude_tracking: '姿态跟踪误差', yaw_tracking: '航向跟踪误差', rate_oscillation: '角速度振荡', vibration: '振动水平',
  motor_saturation: '电机饱和占比', motor_imbalance: '电机输出差异', altitude_tracking: '高度跟踪误差', gps_satellites: '最少卫星数',
  climb_time: '起飞爬升用时',
}
const baseline = computed(() => props.events.find((e) => e.kind === 'baseline_result')?.payload as
  { metrics: Record<string, MetricRow>; diff: Record<string, { current: number; reference: number }> } | undefined)
const experiments = computed(() => props.events.filter((e) => e.kind === 'experiment_result').map((e) => e.payload as
  { restored: string[]; changes: Record<string, { before: number | null; after: number | null; status_after: string }> }))
const conclusion = computed(() => props.events.find((e) => e.kind === 'conclusion')?.payload as
  { root_cause: string; evidence: string; recommendation: string; flights: number; fix: Record<string, { from: number; to: number }> } | undefined)
</script>

<template>
  <div class="card">
    <h2>诊断数据</h2>
    <p v-if="!baseline" class="muted small">基线试飞完成后，这里显示日志指标、参数差异和每次实验前后的对比。</p>
    <template v-else>
      <h3>参数与良好配置的差异</h3>
      <table v-if="Object.keys(baseline.diff).length">
        <thead><tr><th>参数</th><th class="num">当前</th><th class="num">良好配置</th></tr></thead>
        <tbody><tr v-for="(d, k) in baseline.diff" :key="k"><td>{{ k }}</td><td class="num">{{ d.current }}</td><td class="num">{{ d.reference }}</td></tr></tbody>
      </table>
      <p v-else class="muted small">没有差异。</p>
      <h3>日志指标</h3>
      <table>
        <thead>
          <tr><th>指标</th><th class="num">阈值</th><th class="num">基线</th><th v-for="(x, i) in experiments" :key="i" class="num">实验 {{ i + 1 }}<br><small>恢复 {{ x.restored.join('、') }}</small></th></tr>
        </thead>
        <tbody>
          <tr v-for="(m, k) in baseline.metrics" :key="k">
            <td>{{ LABELS[k] ?? k }}</td>
            <td class="num muted">{{ m.threshold ?? '—' }}</td>
            <td class="num" :class="m.status">{{ m.value ?? '—' }} {{ m.unit }}</td>
            <td v-for="(x, i) in experiments" :key="i" class="num" :class="x.changes[k]?.status_after">{{ x.changes[k]?.after ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>
    <div v-if="conclusion" class="conclusion">
      <b>结论：{{ conclusion.root_cause }}</b>
      <p>{{ conclusion.evidence }}</p>
      <p>建议：{{ conclusion.recommendation }}</p>
      <p v-if="Object.keys(conclusion.fix).length" class="small">修改：{{ Object.entries(conclusion.fix).map(([k, v]) => `${k} ${v.from} → ${v.to}`).join('；') }}（已在仿真中验证，共试飞 {{ conclusion.flights }} 次）</p>
    </div>
  </div>
</template>

<style scoped>
h3 { font-size: 13px; margin: 12px 0 6px; color: var(--muted); }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 5px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
th small { font-weight: 400; color: var(--muted); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
td.warn { color: var(--warn); font-weight: 600; }
td.bad { color: var(--bad); font-weight: 600; }
td.ok { color: var(--ok); }
.conclusion { margin-top: 14px; padding: 10px 12px; border-radius: 8px; background: var(--ok-bg); }
.conclusion p { margin: 6px 0 0; line-height: 1.6; font-size: 14px; }
</style>
