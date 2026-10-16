<script setup lang="ts">
import type { Scenario } from '../../scenarios'
import type { Plan } from '../../types'
import PreflightList from './PreflightList.vue'

defineProps<{ plan: Plan; awaiting: boolean; busy: boolean; scenario: Scenario }>()
const emit = defineEmits<{ decide: [approved: boolean] }>()
</script>

<template>
  <div class="card plan" :class="{ awaiting }">
    <h2>{{ awaiting ? '计划待确认' : '执行计划' }}</h2>
    <dl>
      <template v-for="(r, i) in scenario.planRows(plan as any)" :key="i"><dt>{{ r.label }}</dt><dd>{{ r.value }}</dd></template>
    </dl>
    <p v-if="plan.rationale" class="muted small">规划员：{{ plan.rationale }}</p>
    <p class="check" :class="plan.violations?.length ? 'bad' : 'ok'">
      {{ plan.violations?.length ? '安全员：' + plan.violations.map((v) => v.message).join('；') : '安全员：围栏、禁飞区、障碍物净空、高度、电量余量均已校验通过' }}
    </p>
    <details v-if="plan.preflight" class="preflight" :open="plan.preflight.ok === false">
      <summary :class="plan.preflight.ok === false ? 'bad-text' : ''">飞行前检查：{{ plan.preflight.ok === false ? '不通过' : '通过' }}</summary>
      <PreflightList :items="plan.preflight.items" />
      <p v-if="plan.preflight.advice" class="small bad-text">{{ plan.preflight.advice }}</p>
    </details>
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
.preflight { margin-top: 10px; font-size: 13px; }
.preflight summary { cursor: pointer; color: var(--muted); margin-bottom: 4px; }
.bad-text { color: var(--bad); }
</style>
