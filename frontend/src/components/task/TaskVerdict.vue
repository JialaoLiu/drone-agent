<script setup lang="ts">
// 任务结论：用一句话告诉用户这次算不算成功、要不要复检（1.24）。有标准答案时按检出率、精确率判断；
// 没有标准答案（真实使用）时按未确认的疑似数量判断。
import { computed } from 'vue'
import type { Scenario } from '../../scenarios'

const props = defineProps<{ score: Record<string, any> | null; unverified: number; status: string; scenario: Scenario }>()
const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—')
const verdict = computed(() => {
  const s = props.score
  if (props.status !== 'done') return null
  if (s && s.defects != null) {
    const found = Math.round((s.recall ?? 0) * s.defects)
    const missed = s.defects - found
    const fa = s.false_alarms?.length ?? 0
    const wrong = s.wrong_type?.length ?? 0
    if (missed === 0 && fa === 0) return { tone: 'ok', title: `达标：${s.defects} 个真实${props.scenario.target}全部找到，没有误报`,
      advice: wrong ? `其中 ${wrong} 个类型判断有误（见右侧“对照标准答案”），处理前请看照片确认类型。` : '' }
    if (missed > 0) return { tone: 'bad', title: `未达标：${s.defects} 个真实${props.scenario.target}只找到 ${found} 个，漏检 ${missed} 个`,
      advice: '建议复检：用“全面仔细检查”（每个对象都近距复核）重新执行，或在设置里开启变焦分段拍摄。' }
    return { tone: 'warn', title: `全部找到，但有 ${fa} 个误报`, advice: '误报会让运维白跑，可以看下面的误报照片判断原因。' }
  }
  if (props.unverified > 0) return { tone: 'warn', title: `完成，但有 ${props.unverified} 个疑似没有确认`, advice: '请到现场或用照片人工确认。' }
  return { tone: 'ok', title: '完成', advice: '' }
})
</script>

<template>
  <div v-if="verdict" class="verdict card" :class="verdict.tone">
    <b>{{ verdict.title }}</b>
    <p v-if="verdict.advice" class="small">{{ verdict.advice }}</p>
    <div v-if="score && score.defects != null" class="metrics small">
      <span :title="scenario.metricsHelp.recall">检出率 <b>{{ pct(score.recall) }}</b></span>
      <span :title="scenario.metricsHelp.precision">精确率 <b>{{ pct(score.precision) }}</b></span>
      <span>类型正确 <b>{{ pct(score.type_accuracy) }}</b></span>
      <span class="muted">（对照仿真生成时的标准答案；鼠标悬停看指标含义）</span>
    </div>
  </div>
</template>

<style scoped>
.verdict { margin-bottom: 14px; border-left: 5px solid var(--line); }
.verdict.ok { border-left-color: var(--ok); } .verdict.warn { border-left-color: var(--warn); } .verdict.bad { border-left-color: var(--bad); }
.verdict p { margin: 4px 0 0; }
.metrics { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; }
.metrics span[title] { cursor: help; border-bottom: 1px dotted var(--muted); }
</style>
