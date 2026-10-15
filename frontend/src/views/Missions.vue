<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createMission, listMissions, STATUS_LABELS } from '../api'
import { useSim } from '../composables/sim'
import type { MissionSummary } from '../types'

const router = useRouter()
const { sim, ready } = useSim()
const missions = ref<MissionSummary[]>([])
const task = ref('')
const autoApprove = ref(false)
const busy = ref(false)
const error = ref('')

const EXAMPLES = [
  { text: '检查 A 和 C 两列有没有破损或污损', note: '普通任务：只复核可疑的板' },
  { text: '全面仔细检查所有光伏板，一块都不要漏，重点看裂纹', note: '细致任务：每块板近距复核，电量不够自动分架次' },
  { text: '快速扫一遍 B 列，看看有没有明显的问题', note: '快速任务：只看整列照片' },
  { text: '电量保留 40%，检查所有列', note: '指定保留电量' },
  { text: '飞到禁飞区里面拍几张照片', note: '越权请求：会被拒绝' },
]

const GROUPS: Record<string, string[]> = {
  done: ['done'], live: ['planning', 'awaiting_approval', 'running'],
  problem: ['failed', 'cancelled'], blocked: ['rejected', 'clarify', 'unsupported'],
}
const group = ref('')
const sceneFilter = ref('')
const scenes = computed(() => [...new Set(missions.value.map((m) => m.scene))].sort())
const shown = computed(() => missions.value
  .filter((m) => !group.value || GROUPS[group.value].includes(m.status))
  .filter((m) => !sceneFilter.value || m.scene === sceneFilter.value))

async function load() {
  try { missions.value = await listMissions() } catch { missions.value = [] }
}

async function submit() {
  if (!task.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const { id } = await createMission(task.value.trim(), autoApprove.value)
    router.push(`/missions/${id}`)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

const time = (iso: string) => new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
onMounted(load)
</script>

<template>
  <div class="page">
    <section class="card form">
      <h2>下达巡检任务</h2>
      <ol class="steps">
        <li :class="{ done: ready }">
          <b>1. 仿真场景</b>
          <p v-if="ready">当前场景 <b>{{ sim.scene }}</b>，飞控与相机已就绪。<router-link to="/scenes">更换场景</router-link></p>
          <p v-else-if="sim.scene">场景 {{ sim.scene }} 正在启动，约 30 秒。</p>
          <p v-else>仿真未启动，请先到 <router-link to="/scenes">场景库</router-link> 选择一个场景并启动。</p>
        </li>
        <li>
          <b>2. 用一句话描述任务</b>
          <textarea id="task" v-model="task" rows="3" placeholder="例如：检查 A 和 C 两列有没有破损" @keydown.enter.meta="submit" />
          <div class="examples">
            <button v-for="e in EXAMPLES" :key="e.text" class="example" @click="task = e.text">
              <span>{{ e.text }}</span><small>{{ e.note }}</small>
            </button>
          </div>
        </li>
        <li>
          <b>3. 起飞方式</b>
          <label class="small"><input v-model="autoApprove" type="checkbox" /> 计划通过安全校验后自动起飞（不勾选时，需要在任务详情页确认计划）</label>
        </li>
      </ol>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary wide" :disabled="!task.trim() || busy || !ready || !!sim.active_mission" @click="submit">
        {{ sim.active_mission ? '有任务正在执行' : '下达任务' }}
      </button>
    </section>

    <section class="card list">
      <div class="list-head">
        <h2>历史任务</h2>
        <div class="list-tools">
          <select v-model="group">
            <option value="">全部状态</option>
            <option value="done">已完成</option>
            <option value="live">进行中</option>
            <option value="problem">失败或取消</option>
            <option value="blocked">未执行（拒绝、追问）</option>
          </select>
          <select v-model="sceneFilter">
            <option value="">全部场景</option>
            <option v-for="sc in scenes" :key="sc" :value="sc">{{ sc }}</option>
          </select>
          <button class="small" @click="load">刷新</button>
        </div>
      </div>
      <table class="data">
        <thead><tr><th>任务</th><th>场景</th><th>状态</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="m in shown" :key="m.id" class="clickable" @click="router.push(`/missions/${m.id}`)">
            <td>{{ m.task }}</td><td>{{ m.scene }}</td>
            <td><span class="tag" :class="m.status">{{ STATUS_LABELS[m.status] ?? m.status }}</span></td>
            <td class="nowrap">{{ time(m.created_at) }}</td>
          </tr>
          <tr v-if="!shown.length"><td colspan="4" class="muted">{{ missions.length ? '没有符合筛选条件的任务' : '还没有任务' }}</td></tr>
        </tbody>
      </table>
      <p class="small muted">排障任务也在这里，标题是问题描述。</p>
    </section>
  </div>
</template>

<style scoped>
.page { display: grid; grid-template-columns: minmax(320px, 420px) minmax(0, 1fr); gap: 14px; align-items: start; }
.steps { list-style: none; padding: 0; margin: 0 0 12px; display: flex; flex-direction: column; gap: 14px; }
.steps li { border-left: 3px solid var(--line); padding-left: 12px; }
.steps li.done { border-left-color: var(--ok); }
.steps p { margin: 4px 0 0; font-size: 13px; color: var(--muted); }
textarea { width: 100%; margin-top: 6px; resize: vertical; }
.examples { display: flex; flex-direction: column; gap: 4px; margin-top: 6px; }
.example { text-align: left; border-radius: 8px; padding: 5px 9px; display: flex; flex-direction: column; gap: 1px; }
.example span { font-size: 13px; }
.example small { font-size: 11px; color: var(--muted); }
.wide { width: 100%; }
.error { color: var(--bad); font-size: 13px; }
.list-head { display: flex; justify-content: space-between; align-items: center; }
.clickable { cursor: pointer; }
.clickable:hover { background: var(--code); }
.nowrap { white-space: nowrap; }
@media (max-width: 900px) { .page { grid-template-columns: 1fr; } }
.list-tools { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.list-tools select { font-size: 12px; padding: 4px 8px; }
</style>
