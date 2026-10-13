<script setup lang="ts">
import type { Plan } from '../types'

defineProps<{ plan: Plan; awaiting: boolean; busy: boolean }>()
const emit = defineEmits<{ decide: [approved: boolean] }>()

const POLICY: Record<string, string> = {
  none: '只拍整列照片，不做近距复核', flagged: '只复核初筛判为缺陷的板',
  flagged_and_unsure: '复核疑似缺陷和看不清的板', all: '每一块板都近距复核',
}
</script>

<template>
  <div class="card plan" :class="{ awaiting }">
    <h2>{{ awaiting ? '计划待确认' : '执行计划' }}</h2>
    <dl>
      <dt>巡检的列</dt><dd>{{ plan.targets.join('、') }}</dd>
      <dt>复核策略</dt><dd>{{ POLICY[plan.closeup_policy] ?? plan.closeup_policy }}</dd>
      <dt>拍摄距离</dt><dd>{{ plan.standoff_m }} 米</dd>
      <dt>架次</dt>
      <dd>
        <div v-for="(s, i) in plan.sorties" :key="i">第 {{ i + 1 }} 架次：{{ s.join('、') }} 列，耗电约 {{ plan.estimated_battery?.[i]?.toFixed(0) }}%</div>
      </dd>
      <dt>预计时间</dt><dd>{{ plan.estimated_minutes }} 分钟</dd>
    </dl>
    <p v-if="plan.rationale" class="muted small">规划员：{{ plan.rationale }}</p>
    <p class="check" :class="plan.violations?.length ? 'bad' : 'ok'">
      {{ plan.violations?.length ? '安全员：' + plan.violations.map((v) => v.message).join('；') : '安全员：围栏、禁飞区、障碍物净空、高度、电量余量均已校验通过' }}
    </p>
    <div v-if="awaiting" class="actions">
      <button class="primary" :disabled="busy" @click="emit('decide', true)">确认起飞</button>
      <button :disabled="busy" @click="emit('decide', false)">不执行</button>
    </div>
  </div>
</template>

<style scoped>
.plan.awaiting { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 25%, transparent); }
dl { display: grid; grid-template-columns: 72px 1fr; gap: 4px 10px; margin: 0; font-size: 14px; }
dt { color: var(--muted); }
dd { margin: 0; }
.check { font-size: 13px; margin: 10px 0 0; padding: 6px 10px; border-radius: 8px; }
.check.ok { background: var(--ok-bg); color: var(--ok); }
.check.bad { background: var(--bad-bg); color: var(--bad); }
.actions { display: flex; gap: 8px; margin-top: 12px; }
</style>
