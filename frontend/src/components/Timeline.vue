<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { ROLE_LABELS } from '../api'
import type { MissionEvent } from '../types'

const props = defineProps<{ events: MissionEvent[] }>()
const list = ref<HTMLElement>()

watch(() => props.events.length, async () => {
  await nextTick()
  list.value?.scrollTo({ top: list.value.scrollHeight, behavior: 'smooth' })
})

const time = (iso: string) => new Date(iso).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const tone = (e: MissionEvent) =>
  ['violation', 'error', 'rejected', 'skip_arrays', 'trim_closeups'].includes(e.kind) ? 'bad'
    : ['awaiting_approval', 'defer_arrays', 'clarify', 'unsupported'].includes(e.kind) ? 'warn' : ''
</script>

<template>
  <ol ref="list" class="timeline">
    <li v-for="e in events" :key="e.seq" :class="tone(e)">
      <span class="role" :class="e.role">{{ ROLE_LABELS[e.role] ?? e.role }}</span>
      <span class="time">{{ time(e.created_at) }}</span>
      <p>{{ e.message }}</p>
    </li>
    <li v-if="!events.length" class="muted">还没有记录</li>
  </ol>
</template>

<style scoped>
.timeline { list-style: none; margin: 0; padding: 0; max-height: 460px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
li { border-left: 3px solid var(--line); padding: 2px 0 2px 10px; }
li.bad { border-left-color: var(--bad); }
li.warn { border-left-color: var(--warn); }
p { margin: 3px 0 0; font-size: 14px; line-height: 1.55; }
.time { color: var(--muted); font-size: 12px; margin-left: 6px; font-variant-numeric: tabular-nums; }
.role { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--code); }
.role.coordinator { background: #e0e7ff; color: #3730a3; }
.role.planner { background: #dbeafe; color: #1e40af; }
.role.safety { background: #fee2e2; color: #991b1b; }
.role.pilot { background: #dcfce7; color: #166534; }
.role.inspector { background: #fef3c7; color: #92400e; }
.role.reporter { background: #f3e8ff; color: #6b21a8; }
.role.diagnostician { background: #ffe4e6; color: #9f1239; }
@media (prefers-color-scheme: dark) {
  .role.coordinator, .role.planner, .role.safety, .role.pilot, .role.inspector, .role.reporter, .role.diagnostician { filter: brightness(0.75) saturate(1.3); }
}
</style>
