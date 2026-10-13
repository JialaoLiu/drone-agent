<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getUsageDetail, ROLE_LABELS } from '../api'
import BarChart from '../components/BarChart.vue'

const router = useRouter()
const days = ref(30)
const data = ref<Record<string, any> | null>(null)
const error = ref('')

async function load() {
  try { data.value = await getUsageDetail(days.value); error.value = '' } catch (e) { error.value = (e as Error).message }
}
watch(days, load)
onMounted(load)

const total = computed(() => data.value?.total ?? {})
const hitRate = computed(() => (total.value.prompt_tokens ? total.value.cache_hit_tokens / total.value.prompt_tokens : null))
const costByDay = computed(() => (data.value?.by_day ?? []).map((d: any) => ({ label: d.day.slice(5), value: d.cost_yuan })))
const callsByDay = computed(() => (data.value?.by_day ?? []).map((d: any) => ({ label: d.day.slice(5), value: d.calls, color: '#16a085' })))
const costByRole = computed(() => (data.value?.by_role ?? []).map((r: any) => ({ label: ROLE_LABELS[r.role] ?? r.role, value: r.cost_yuan, color: '#9b59d0' })))
const n = (v: number | null | undefined, d = 0) => (v == null ? '—' : v.toLocaleString('zh-CN', { maximumFractionDigits: d }))
const yuan = (v: number | null | undefined) => (v == null ? '—' : `¥${v.toFixed(v < 0.01 ? 5 : 4)}`)
const time = (iso: string | null) => (iso ? new Date(iso).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—')
</script>

<template>
  <div class="page-head">
    <p>每次模型调用都记录 Token、缓存命中、图片数、耗时与费用。费用按 DeepSeek 官网单价与高峰 / 空闲时段计算（backend/src/droneagent/pricing.yaml）。</p>
    <div class="ranges">
      <button v-for="d in [1, 7, 30, 90]" :key="d" :class="{ on: days === d }" @click="days = d">{{ d === 1 ? '24 小时' : `${d} 天` }}</button>
    </div>
  </div>
  <p v-if="error" class="notice">{{ error }}</p>
  <template v-if="data">
    <div class="stats">
      <div class="stat"><span>模型调用</span><b>{{ n(total.calls) }}</b><small>平均耗时 {{ n(total.avg_ms) }} 毫秒</small></div>
      <div class="stat"><span>输入 Token</span><b>{{ n(total.prompt_tokens) }}</b><small>缓存命中率 {{ hitRate == null ? '—' : (hitRate * 100).toFixed(1) + '%' }}</small></div>
      <div class="stat"><span>输出 Token</span><b>{{ n(total.completion_tokens) }}</b><small>含模型推理过程的 Token</small></div>
      <div class="stat"><span>图片</span><b>{{ n(total.images) }}</b><small>交给视觉模型的拼图与近照</small></div>
      <div class="stat"><span>费用</span><b>{{ yuan(total.cost_yuan) }}</b><small>{{ days === 1 ? '24 小时' : `近 ${days} 天` }}（含评测）</small></div>
    </div>

    <div class="grid2 gap">
      <section class="card"><h2>每日费用（元）</h2><BarChart :items="costByDay" :digits="3" /></section>
      <section class="card"><h2>每日调用次数</h2><BarChart :items="callsByDay" /></section>
    </div>

    <div class="grid2 gap">
      <section class="card">
        <h2>按角色</h2>
        <BarChart :items="costByRole" :digits="3" :height="140" />
        <table class="data">
          <thead><tr><th>角色</th><th class="num">调用</th><th class="num">输入</th><th class="num">输出</th><th class="num">图片</th><th class="num">平均耗时</th><th class="num">费用</th></tr></thead>
          <tbody>
            <tr v-for="r in data.by_role" :key="r.role">
              <td>{{ ROLE_LABELS[r.role] ?? r.role }}</td><td class="num">{{ n(r.calls) }}</td><td class="num">{{ n(r.prompt_tokens) }}</td>
              <td class="num">{{ n(r.completion_tokens) }}</td><td class="num">{{ n(r.images) }}</td><td class="num">{{ n(r.avg_ms) }} ms</td><td class="num">{{ yuan(r.cost_yuan) }}</td>
            </tr>
          </tbody>
        </table>
      </section>
      <section class="card">
        <h2>按模型</h2>
        <table class="data">
          <thead><tr><th>模型（接口返回的实际模型）</th><th class="num">调用</th><th class="num">输入</th><th class="num">输出</th><th class="num">费用</th></tr></thead>
          <tbody>
            <tr v-for="r in data.by_model" :key="r.model"><td>{{ r.model }}</td><td class="num">{{ n(r.calls) }}</td><td class="num">{{ n(r.prompt_tokens) }}</td><td class="num">{{ n(r.completion_tokens) }}</td><td class="num">{{ yuan(r.cost_yuan) }}</td></tr>
          </tbody>
        </table>
        <p class="small muted">视觉模型（巡检员）每张图最多按 384 Token 计；文字模型用于调度、规划、报告与诊断。</p>
      </section>
    </div>

    <section class="card gap">
      <h2>按任务</h2>
      <table class="data">
        <thead><tr><th>任务</th><th>来源</th><th>时间</th><th class="num">调用</th><th class="num">图片</th><th class="num">费用</th></tr></thead>
        <tbody>
          <tr v-for="r in data.by_mission" :key="r.mission_id ?? 'none'" :class="{ clickable: r.source === '任务' && r.mission_id }"
              @click="r.source === '任务' && r.mission_id && router.push(`/missions/${r.mission_id}`)">
            <td>{{ r.task ?? '（无任务记录）' }}</td><td><span class="tag" :class="r.source === '评测' ? 'warn' : ''">{{ r.source }}</span></td>
            <td class="muted">{{ time(r.created_at) }}</td><td class="num">{{ n(r.calls) }}</td><td class="num">{{ n(r.images) }}</td><td class="num">{{ yuan(r.cost_yuan) }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </template>
</template>

<style scoped>
.ranges { display: flex; gap: 6px; }
.ranges button.on { border-color: var(--accent); color: var(--accent); }
.gap { margin-top: 14px; }
.clickable { cursor: pointer; }
.clickable:hover { background: var(--code); }
.notice { padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); }
</style>
