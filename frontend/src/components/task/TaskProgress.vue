<script setup lang="ts">
// 任务进度：阶段条 + 当前正在做的事（带动效），让等待有反馈（1.10、1.14）
import { computed } from 'vue'
import { ROLE_LABELS } from '../../api'
import type { MissionEvent } from '../../types'
import ThinkingText from '../common/ThinkingText.vue'

const props = defineProps<{ events: MissionEvent[]; status: string; stages: { key: string; label: string; kinds: string[] }[] }>()
const reached = computed(() => {
  const kinds = new Set(props.events.map((e) => e.kind))
  let last = -1
  props.stages.forEach((s, i) => { if (s.kinds.some((k) => kinds.has(k))) last = i })
  return props.status === 'done' ? props.stages.length - 1 : last
})
const latest = computed(() => props.events[props.events.length - 1])
const WAITING: Record<string, string> = { queued: '排队中，前面的任务结束后自动开始', planning: '正在理解任务并规划', awaiting_approval: '等待你确认计划', running: '执行中' }
</script>

<template>
  <div class="progress card">
    <ol class="stages">
      <li v-for="(s, i) in stages" :key="s.key" :class="{ done: i < reached || (i === reached && status === 'done'), now: i === reached && status !== 'done', next: i > reached }">
        <i />{{ s.label }}
      </li>
    </ol>
    <p v-if="WAITING[status]" class="now-line">
      <span v-if="latest" class="role">{{ ROLE_LABELS[latest.role] ?? latest.role }}</span>
      <ThinkingText :text="latest?.message ?? WAITING[status]" />
    </p>
  </div>
</template>

<style scoped>
.progress { padding: 10px 14px; margin-bottom: 14px; }
.stages { list-style: none; display: flex; gap: 6px; padding: 0; margin: 0; flex-wrap: wrap; }
.stages li { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); padding: 3px 10px 3px 6px; border-radius: 999px; background: var(--code); }
.stages i { width: 8px; height: 8px; border-radius: 50%; background: var(--line); }
.stages .done { color: var(--ok); } .stages .done i { background: var(--ok); }
.stages .now { color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
.stages .now i { background: var(--accent); animation: blink 1s ease-in-out infinite; }
.now-line { margin: 10px 0 0; font-size: 14px; display: flex; gap: 8px; align-items: baseline; }
.role { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--code); white-space: nowrap; }
@keyframes blink { 50% { opacity: 0.3; } }
</style>
