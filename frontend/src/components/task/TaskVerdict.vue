<script setup lang="ts">
// 任务结论：一句话说明这次算不算成功、下一步做什么（1.24）。判断规则由各场景在注册表里给出。
import { computed } from 'vue'
import type { Scenario } from '../../scenarios'

const props = defineProps<{ score: Record<string, any> | null; result: Record<string, any> | null; unverified: number; status: string; scenario: Scenario }>()
const verdict = computed(() => (props.status === 'done' ? props.scenario.verdict(props.score, props.result, props.unverified) : null))
</script>

<template>
  <div v-if="verdict" class="verdict card" :class="verdict.tone">
    <b>{{ verdict.title }}</b>
    <p v-if="verdict.advice" class="small">{{ verdict.advice }}</p>
    <div v-if="verdict.metrics.length" class="metrics small">
      <span v-for="m in verdict.metrics" :key="m.label" :title="m.help" :class="{ help: m.help }">{{ m.label }} <b>{{ m.value }}</b></span>
      <span v-if="score" class="muted">（对照仿真生成时的标准答案；悬停看指标含义）</span>
    </div>
  </div>
</template>

<style scoped>
.verdict { margin-bottom: 14px; border-left: 5px solid var(--line); }
.verdict.ok { border-left-color: var(--ok); } .verdict.warn { border-left-color: var(--warn); } .verdict.bad { border-left-color: var(--bad); }
.verdict p { margin: 4px 0 0; }
.metrics { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; }
.help { cursor: help; border-bottom: 1px dotted var(--muted); }
</style>
