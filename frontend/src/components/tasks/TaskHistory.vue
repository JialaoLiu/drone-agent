<script setup lang="ts">
// 历史任务：搜索、按状态与类型筛选；进行中的任务带动效；点击打开（1.12、1.35）
import { computed, ref } from 'vue'
import { LIVE_STATUSES, STATUS_LABELS } from '../../api'
import type { MissionSummary } from '../../types'
import Spinner from '../common/Spinner.vue'

const props = defineProps<{ missions: MissionSummary[]; selected: string | null; kind?: 'mission' | 'troubleshoot' }>()
const emit = defineEmits<{ open: [id: string] }>()
const q = ref('')
const group = ref<'all' | 'live' | 'done' | 'problem'>('all')
const GROUPS: Record<string, string[]> = { live: LIVE_STATUSES, done: ['done'], problem: ['failed', 'cancelled', 'rejected', 'clarify', 'unsupported'] }
const shown = computed(() => props.missions
  .filter((m) => !props.kind || (m.kind ?? 'mission') === props.kind)
  .filter((m) => group.value === 'all' || GROUPS[group.value].includes(m.status))
  .filter((m) => !q.value.trim() || m.task.includes(q.value.trim()) || m.scene.includes(q.value.trim()) || m.id.startsWith(q.value.trim())))
const time = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
</script>

<template>
  <section class="history">
    <div class="tools">
      <input v-model="q" placeholder="搜索任务、场景" />
      <div class="groups">
        <button v-for="(label, k) in { all: '全部', live: '进行中', done: '已完成', problem: '有问题' }" :key="k" class="small" :class="{ on: group === k }" @click="group = k as any">{{ label }}</button>
      </div>
    </div>
    <ul>
      <li v-for="m in shown" :key="m.id" :class="{ on: m.id === selected }" @click="emit('open', m.id)">
        <div class="line">
          <span class="tag" :class="m.status"><Spinner v-if="LIVE_STATUSES.includes(m.status)" :size="9" /> {{ STATUS_LABELS[m.status] ?? m.status }}</span>
          <span v-if="m.kind === 'troubleshoot' && !kind" class="tag">诊断</span>
          <span class="muted small time">{{ time(m.created_at) }}</span>
        </div>
        <p>{{ m.task }}</p>
        <span class="muted small">{{ m.scene }} · {{ m.id }}</span>
      </li>
      <li v-if="!shown.length" class="empty muted small">{{ missions.length ? '没有符合条件的任务' : '还没有任务' }}</li>
    </ul>
  </section>
</template>

<style scoped>
.history { display: flex; flex-direction: column; min-height: 0; flex: 1; }
.tools { padding: 10px 14px; display: flex; flex-direction: column; gap: 6px; }
.tools input { width: 100%; }
.groups { display: flex; gap: 4px; flex-wrap: wrap; }
.groups .on { border-color: var(--accent); color: var(--accent); }
ul { list-style: none; margin: 0; padding: 0 8px 12px; overflow-y: auto; flex: 1; }
li { padding: 8px 8px; border-radius: 8px; cursor: pointer; border: 1px solid transparent; }
li:hover { background: var(--code); }
li.on { background: color-mix(in srgb, var(--accent) 10%, transparent); border-color: color-mix(in srgb, var(--accent) 35%, transparent); }
.line { display: flex; gap: 6px; align-items: center; }
.time { margin-left: auto; }
p { margin: 4px 0 2px; font-size: 13px; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.empty { cursor: default; }
</style>
