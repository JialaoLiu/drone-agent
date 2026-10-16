<script setup lang="ts">
// 对照标准答案逐条列出：漏检、误报、类型错，点击在地图上定位（1.36）
import { computed } from 'vue'
import type { Scenario } from '../../scenarios'
import type { Truth } from '../../types'

const props = defineProps<{ score: Record<string, any>; truth: Truth | null; scenario: Scenario }>()
const emit = defineEmits<{ select: [id: string] }>()
const truthType = (id: string) => props.truth?.defects.find((d) => d.panel_id === id)
const label = (t?: string | null) => (t ? props.scenario.findingTypes[t] ?? t : '—')
const rows = computed(() => [
  ...(props.score.missed ?? []).map((id: string) => ({ id, kind: 'missed', text: `漏检：实际是${label(truthType(id)?.type)}${truthType(id)?.severity === 'minor' ? '（轻微）' : ''}，没有报出` })),
  ...(props.score.false_alarms ?? []).map((id: string) => ({ id, kind: 'false', text: '误报：实际正常，被报成缺陷' })),
  ...(props.score.wrong_type ?? []).map((w: any) => ({ id: w.panel, kind: 'wrong', text: `类型错：实际是${label(w.truth)}，报成${label(w.reported)}` })),
])
</script>

<template>
  <div class="card cmp">
    <h2>对照标准答案</h2>
    <p v-if="!rows.length" class="small ok-text">没有漏检、误报和类型错误。</p>
    <ul v-else>
      <li v-for="r in rows" :key="r.kind + r.id" :class="r.kind" @click="emit('select', r.id)"><b>{{ r.id }}</b><span>{{ r.text }}</span></li>
    </ul>
    <p class="small muted">标准答案来自仿真生成场景时记录的缺陷位置；真实使用时没有标准答案。点击一行在地图上定位。</p>
  </div>
</template>

<style scoped>
.cmp { margin-bottom: 14px; }
ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 6px; }
li { display: flex; gap: 10px; align-items: baseline; padding: 6px 10px; border-radius: 8px; background: var(--code); cursor: pointer; border-left: 4px solid; font-size: 13px; }
li.missed { border-color: #d6336c; } li.false { border-color: #f59f00; } li.wrong { border-color: #7048e8; }
</style>
