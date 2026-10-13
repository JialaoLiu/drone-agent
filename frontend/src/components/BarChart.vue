<script setup lang="ts">
import { computed } from 'vue'

// 轻量柱状图（SVG），不引入图表库
const props = withDefaults(defineProps<{ items: { label: string; value: number; color?: string }[]; unit?: string; height?: number; digits?: number }>(),
  { unit: '', height: 180, digits: 0 })
const max = computed(() => Math.max(1e-9, ...props.items.map((i) => i.value)))
const W = 600
const barW = computed(() => Math.min(56, (W - 20) / Math.max(1, props.items.length) - 8))
const fmt = (v: number) => v.toLocaleString('zh-CN', { maximumFractionDigits: props.digits })
</script>

<template>
  <svg :viewBox="`0 0 ${W} ${height + 40}`" class="chart" role="img">
    <line x1="10" :y1="height" :x2="W - 10" :y2="height" stroke="var(--line)" />
    <g v-for="(it, i) in items" :key="it.label">
      <rect :x="16 + i * (barW + 8)" :y="height - (it.value / max) * (height - 22)" :width="barW" :height="(it.value / max) * (height - 22)"
            rx="4" :fill="it.color ?? 'var(--accent)'" opacity="0.88"><title>{{ it.label }}：{{ fmt(it.value) }}{{ unit }}</title></rect>
      <text :x="16 + i * (barW + 8) + barW / 2" :y="height - (it.value / max) * (height - 22) - 5" class="v">{{ fmt(it.value) }}</text>
      <text :x="16 + i * (barW + 8) + barW / 2" :y="height + 16" class="l">{{ it.label }}</text>
    </g>
    <text v-if="!items.length" :x="W / 2" :y="height / 2" class="l">暂无数据</text>
  </svg>
</template>

<style scoped>
.chart { width: 100%; height: auto; display: block; }
.v { font-size: 11px; fill: var(--muted); text-anchor: middle; font-variant-numeric: tabular-nums; }
.l { font-size: 11px; fill: var(--muted); text-anchor: middle; }
</style>
