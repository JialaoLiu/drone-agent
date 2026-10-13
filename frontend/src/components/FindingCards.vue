<script setup lang="ts">
import { computed, ref } from 'vue'
import { DEFECT_LABELS, panelUrl } from '../api'
import type { Finding } from '../types'

const props = defineProps<{ findings: Finding[]; selected: string | null; score: Record<string, any> | null }>()
const emit = defineEmits<{ select: [panelId: string]; photo: [photoId: number] }>()

const STATUS: Record<string, string> = { confirmed: '确认缺陷', suspect: '待确认', unverified: '待确认', cleared: '复核后排除' }
const PRIORITY: Record<string, number> = { broken: 0, missing: 0, crack: 1, soiling: 2 }
const filter = ref<'all' | 'confirmed' | 'open' | 'cleared'>('all')
const typeFilter = ref('')
const counts = computed(() => ({
  confirmed: props.findings.filter((f) => f.status === 'confirmed').length,
  open: props.findings.filter((f) => f.status === 'suspect' || f.status === 'unverified').length,
  cleared: props.findings.filter((f) => f.status === 'cleared').length,
}))
const shown = computed(() => props.findings
  .filter((f) => filter.value === 'all' || (filter.value === 'open' ? ['suspect', 'unverified'].includes(f.status) : f.status === filter.value))
  .filter((f) => !typeFilter.value || f.type === typeFilter.value)
  .sort((a, b) => ['confirmed', 'suspect', 'unverified', 'cleared'].indexOf(a.status) - ['confirmed', 'suspect', 'unverified', 'cleared'].indexOf(b.status)
    || (PRIORITY[a.type ?? ''] ?? 3) - (PRIORITY[b.type ?? ''] ?? 3) || a.panel_id.localeCompare(b.panel_id)))
const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—')
const tilePhoto = (f: Finding) => f.verify_photo ?? f.screen_photo
</script>

<template>
  <div class="card">
    <div class="head">
      <h2>巡检发现</h2>
      <div class="filters">
        <button :class="{ on: filter === 'all' }" @click="filter = 'all'">全部 {{ findings.length }}</button>
        <button :class="{ on: filter === 'confirmed' }" @click="filter = 'confirmed'">确认 {{ counts.confirmed }}</button>
        <button :class="{ on: filter === 'open' }" @click="filter = 'open'">待确认 {{ counts.open }}</button>
        <button :class="{ on: filter === 'cleared' }" @click="filter = 'cleared'">已排除 {{ counts.cleared }}</button>
        <select v-model="typeFilter">
          <option value="">全部类型</option>
          <option v-for="(v, k) in DEFECT_LABELS" :key="k" :value="k">{{ v }}</option>
        </select>
      </div>
    </div>
    <div v-if="score" class="score">
      <span>对照标准答案</span>
      <b>检出率 {{ pct(score.recall) }}</b><b>精确率 {{ pct(score.precision) }}</b><b>类型正确 {{ pct(score.type_accuracy) }}</b>
      <span v-if="score.missed?.length" class="muted">漏检 {{ score.missed.join('、') }}</span>
      <span v-if="score.false_alarms?.length" class="muted">误报 {{ score.false_alarms.join('、') }}</span>
    </div>
    <div v-if="shown.length" class="cards">
      <article v-for="f in shown" :key="f.panel_id" :class="[f.status, { on: f.panel_id === selected }]" @click="emit('select', f.panel_id)">
        <img v-if="tilePhoto(f)" :src="panelUrl(tilePhoto(f)!, f.panel_id)" :alt="f.panel_id" loading="lazy" />
        <div v-else class="noimg">无照片</div>
        <div class="body">
          <div class="row"><b>{{ f.panel_id }}</b><span class="tag" :class="f.status === 'confirmed' ? 'bad' : f.status === 'cleared' ? '' : 'warn'">{{ STATUS[f.status] }}</span></div>
          <div class="row small"><span>{{ f.type ? DEFECT_LABELS[f.type] ?? f.type : '—' }}</span><span class="muted">置信度 {{ f.confidence != null ? f.confidence.toFixed(2) : '—' }}</span></div>
          <p class="evidence">{{ f.evidence }}</p>
          <div class="links">
            <button v-if="f.screen_photo" class="link" @click.stop="emit('photo', f.screen_photo)">整列照片</button>
            <button v-if="f.verify_photo" class="link" @click.stop="emit('photo', f.verify_photo)">近照</button>
          </div>
        </div>
      </article>
    </div>
    <p v-else class="muted small">{{ findings.length ? '没有符合筛选条件的发现。' : '还没有发现。整列照片初筛后，疑似缺陷会出现在这里。' }}</p>
  </div>
</template>

<style scoped>
.head { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px; }
.head h2 { margin: 0; }
.filters { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.filters button { font-size: 12px; padding: 4px 10px; }
.filters button.on { border-color: var(--accent); color: var(--accent); }
.filters select { font-size: 12px; padding: 4px 8px; }
.score { display: flex; flex-wrap: wrap; gap: 6px 14px; font-size: 13px; margin-bottom: 10px; padding: 8px 10px; border-radius: 8px; background: var(--code); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 10px; }
article { display: flex; gap: 10px; border: 1px solid var(--line); border-left: 4px solid var(--line); border-radius: 10px; padding: 8px; cursor: pointer; background: var(--panel); }
article.confirmed { border-left-color: var(--bad); }
article.suspect, article.unverified { border-left-color: var(--warn); }
article.cleared { opacity: 0.65; }
article.on { box-shadow: 0 0 0 2px var(--accent); }
img, .noimg { width: 64px; height: 109px; object-fit: cover; border-radius: 6px; flex-shrink: 0; background: var(--code); }
.noimg { display: flex; align-items: center; justify-content: center; font-size: 11px; color: var(--muted); }
.body { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.row { display: flex; justify-content: space-between; gap: 6px; align-items: center; }
.evidence { margin: 2px 0 0; font-size: 12px; color: var(--muted); line-height: 1.5; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.links { margin-top: auto; }
.link { border: 0; background: transparent; color: var(--accent); padding: 0 6px 0 0; font-size: 12px; }
</style>
