<script setup lang="ts">
import { computed } from 'vue'
import { DEFECT_LABELS } from '../api'
import type { Finding } from '../types'

const props = defineProps<{ findings: Finding[]; selected: string | null; score: Record<string, unknown> | null }>()
const emit = defineEmits<{ select: [panelId: string]; photo: [photoId: number] }>()

const STATUS: Record<string, string> = { confirmed: '确认缺陷', suspect: '疑似（未复核）', unverified: '疑似（未复核）', cleared: '复核后排除' }
const order = ['confirmed', 'suspect', 'unverified', 'cleared']
const sorted = computed(() => [...props.findings].sort((a, b) => order.indexOf(a.status) - order.indexOf(b.status) || a.panel_id.localeCompare(b.panel_id)))
const counts = computed(() => ({
  confirmed: props.findings.filter((f) => f.status === 'confirmed').length,
  open: props.findings.filter((f) => f.status === 'suspect' || f.status === 'unverified').length,
  cleared: props.findings.filter((f) => f.status === 'cleared').length,
}))
const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—')
</script>

<template>
  <div class="card">
    <h2>巡检发现 <span class="muted small">确认 {{ counts.confirmed }} · 待确认 {{ counts.open }} · 排除 {{ counts.cleared }}</span></h2>
    <div v-if="score" class="score">
      <span>对照标准答案：检出率 <b>{{ pct(score.recall) }}</b></span>
      <span>精确率 <b>{{ pct(score.precision) }}</b></span>
      <span>类型正确 <b>{{ pct(score.type_accuracy) }}</b></span>
      <span v-if="(score.missed as string[]).length" class="muted">漏检 {{ (score.missed as string[]).join('、') }}</span>
      <span v-if="(score.false_alarms as string[]).length" class="muted">误报 {{ (score.false_alarms as string[]).join('、') }}</span>
    </div>
    <table v-if="sorted.length">
      <thead><tr><th>板</th><th>结论</th><th>类型</th><th class="num">置信度</th><th>依据</th><th>照片</th></tr></thead>
      <tbody>
        <tr v-for="f in sorted" :key="f.panel_id" :class="[f.status, { on: f.panel_id === selected }]" @click="emit('select', f.panel_id)">
          <td class="id">{{ f.panel_id }}</td>
          <td><span class="tag" :class="f.status">{{ STATUS[f.status] }}</span></td>
          <td>{{ f.type ? DEFECT_LABELS[f.type] ?? f.type : '—' }}</td>
          <td class="num">{{ f.confidence != null ? f.confidence.toFixed(2) : '—' }}</td>
          <td class="evidence">{{ f.evidence }}</td>
          <td class="links">
            <button v-if="f.screen_photo" class="link" @click.stop="emit('photo', f.screen_photo)">整列</button>
            <button v-if="f.verify_photo" class="link" @click.stop="emit('photo', f.verify_photo)">近照</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="muted small">还没有发现。整列照片初筛后，疑似缺陷会出现在这里。</p>
  </div>
</template>

<style scoped>
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { text-align: left; padding: 6px 6px; border-bottom: 1px solid var(--line); vertical-align: top; }
tr { cursor: pointer; }
tr.on { background: var(--code); }
tr.cleared { opacity: 0.6; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.id { font-variant-numeric: tabular-nums; white-space: nowrap; font-weight: 600; }
.evidence { color: var(--muted); min-width: 180px; }
.tag { font-size: 12px; padding: 1px 8px; border-radius: 999px; white-space: nowrap; }
.tag.confirmed { background: var(--bad-bg); color: var(--bad); }
.tag.suspect, .tag.unverified { background: var(--warn-bg); color: var(--warn); }
.tag.cleared { background: var(--code); color: var(--muted); }
.links { white-space: nowrap; }
.link { border: 0; background: transparent; color: var(--accent); padding: 0 4px; font-size: 13px; }
.score { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 13px; margin-bottom: 8px; padding: 8px 10px; border-radius: 8px; background: var(--code); }
</style>
