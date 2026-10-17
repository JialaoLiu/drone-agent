<script setup lang="ts">
// 场景库：顶部固定操作栏（生成场景、启动选中的场景），卡片按创建时间倒序、可筛选、分页，点击卡片选中、可预览（1.6、1.9、1.26、1.28、1.30、1.38）
import { computed, onMounted, ref } from 'vue'
import { createScene, getTruth, listScenes } from '../api'
import Modal from '../components/common/Modal.vue'
import Spinner from '../components/common/Spinner.vue'
import MapView from '../components/map/MapView.vue'
import PlaceSearch from '../components/scene/PlaceSearch.vue'
import SceneThumb from '../components/scene/SceneThumb.vue'
import { allScenarios, scenarioOf } from '../scenarios'
import { useSim } from '../stores/sim'
import { useSites } from '../stores/sites'
import { useToast } from '../stores/toast'
import type { Place, SceneInfo, Truth } from '../types'

const { sim, phase, start, stop } = useSim()
const { cache, load } = useSites()
const { show } = useToast()
const scenes = ref<SceneInfo[]>([])
const selected = ref<string | null>(null)
const place = ref<Place | null>(null)
const seed = ref<number | null>(null)
const kind = ref<'solar' | 'rescue'>('solar')
const environment = ref('')            // 救援随机场景的环境类型；空为按布局编号随机
const ENVIRONMENTS = { park: '公园湖泊', forest: '山林', riverside: '河岸', plaza: '城市广场' }
const advanced = ref(false)
const generating = ref(false)
const filter = ref<'all' | 'real' | 'random' | 'solar' | 'rescue'>('all')
const page = ref(1)
const PER = 12
const preview = ref<string | null>(null)
const truth = ref<Truth | null>(null)

const filtered = computed(() => scenes.value.filter((s) => {
  if (filter.value === 'all') return true
  if (filter.value === 'real') return !!s.location
  if (filter.value === 'random') return !s.location
  return (s.scenario ?? 'solar') === filter.value
}))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / PER)))
const shown = computed(() => filtered.value.slice((page.value - 1) * PER, page.value * PER))
const starting = computed(() => phase.value === 'starting')

async function refresh() {
  scenes.value = await listScenes().catch(() => [])
  await Promise.all(shown.value.map((s) => load(s.name)))
}
async function generate() {
  generating.value = true
  try {
    const { name } = await createScene(advanced.value ? seed.value : null, place.value, kind.value, kind.value === 'rescue' && !place.value ? environment.value || null : null)
    show(`已生成场景 ${name}${place.value ? `（${place.value.name}）` : ''}`, 'ok')
    filter.value = 'all'; page.value = 1
    await refresh()
    selected.value = name
  } catch (e) { show((e as Error).message, 'bad') } finally { generating.value = false }
}
async function setPage(p: number) { page.value = p; await Promise.all(shown.value.map((s) => load(s.name))) }
async function openPreview(name: string) { preview.value = name; truth.value = null; await load(name) }
async function toggleTruth() { truth.value = truth.value ? null : await getTruth(preview.value!).catch(() => null) }
const info = (s: SceneInfo) => `${scenarioOf(s.scenario).label}${s.environment ? `（${s.environment}）` : ''} · ${scenarioOf(s.scenario).summary(null, { arrays: s.arrays, panels: s.panels })}`
const time = (t?: number) => (t ? new Date(t * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '')
onMounted(refresh)
</script>

<template>
  <div class="bar card">
    <div class="gen">
      <select v-model="kind" title="应用场景">
        <option v-for="sc in allScenarios()" :key="sc.id" :value="sc.id">{{ sc.label }}</option>
      </select>
      <select v-if="kind === 'rescue' && !place" v-model="environment" title="环境类型（真实场地按当地实际）">
        <option value="">环境随机</option>
        <option v-for="(l, k) in ENVIRONMENTS" :key="k" :value="k">{{ l }}</option>
      </select>
      <PlaceSearch v-model="place" />
      <button class="primary" :disabled="generating" @click="generate"><Spinner v-if="generating" :size="12" /> {{ place ? '在这里生成场景' : '随机生成场景' }}</button>
      <button class="small link" @click="advanced = !advanced">{{ advanced ? '收起' : '高级' }}</button>
    </div>
    <div v-if="advanced" class="adv small">
      布局编号 <input v-model.number="seed" type="number" min="1" max="100000" placeholder="留空随机" />
      <span class="muted">同一个编号生成完全相同的布局（阵列、禁飞区、缺陷），用于复现；留空每次随机。</span>
    </div>
    <div class="sel">
      <span v-if="selected">已选 <b>{{ selected }}</b></span><span v-else class="muted small">点选下面的场景卡片</span>
      <button class="primary" :disabled="!selected || starting || !!sim.active_mission" @click="start(selected!)">
        <Spinner v-if="starting && sim.job?.scene === selected" :size="12" /> {{ sim.scene === selected ? '重启仿真' : '启动仿真' }}
      </button>
      <button :disabled="!selected" @click="openPreview(selected!)">预览</button>
      <span class="divider" />
      <span class="small muted">运行中：{{ sim.scene ?? (starting ? `${sim.job?.scene}（启动中）` : '无') }}</span>
      <button v-if="sim.scene || starting" class="small" :disabled="!!sim.active_mission" @click="stop">停止</button>
    </div>
  </div>
  <p class="small muted note">场景按随机布局编号生成：围栏、禁飞区、阵列的数量与朝向、树的位置、缺陷与干扰都随编号变化，并附标准答案。真实场地会取用当地的水面、建筑、树（OpenStreetMap）和高程，阵列不会放在水面和建筑上。场景里的光伏板是程序生成的贴图，不是数据集照片。</p>

  <div class="filters">
    <button v-for="(l, k) in { all: '全部', real: '真实场地', random: '随机场地', solar: '光伏巡检', rescue: '应急救援' }" :key="k" class="small" :class="{ on: filter === k }" @click="filter = k as any; setPage(1)">{{ l }}</button>
    <span class="muted small">共 {{ filtered.length }} 个，新生成的在前</span>
  </div>
  <div class="cards">
    <article v-for="s in shown" :key="s.name" class="card" :class="{ on: selected === s.name, running: sim.scene === s.name }" @click="selected = s.name" @dblclick="openPreview(s.name)">
      <SceneThumb v-if="cache[s.name]" :site="cache[s.name]" />
      <div v-else class="ph" />
      <div class="meta">
        <b>{{ s.name }}</b>
        <span v-if="sim.scene === s.name" class="tag ok">运行中</span>
        <span v-else-if="starting && sim.job?.scene === s.name" class="tag warn">启动中</span>
      </div>
      <p class="small muted">{{ s.location ?? '随机场地' }} · {{ info(s) }}</p>
      <p class="small muted">{{ time(s.created_at) }}</p>
    </article>
  </div>
  <div v-if="pages > 1" class="pager">
    <button class="small" :disabled="page === 1" @click="setPage(page - 1)">上一页</button>
    <span class="small">{{ page }} / {{ pages }}</span>
    <button class="small" :disabled="page === pages" @click="setPage(page + 1)">下一页</button>
  </div>

  <Modal v-if="preview && cache[preview]" :title="`${preview} 预览`" @close="preview = null">
    <div class="pv-head">
      <button class="small" @click="toggleTruth">{{ truth ? '隐藏标准答案' : '显示标准答案' }}</button>
      <button class="small primary" :disabled="starting || !!sim.active_mission" @click="start(preview!); preview = null">启动这个场景</button>
    </div>
    <MapView :site="cache[preview]" :findings="[]" :plan="null" :telemetry="{}" :truth="truth" />
    <p v-if="truth" class="small muted">标准答案：{{ truth.defects.map((d) => `${d.panel_id} ${d.label}`).join('；') }}</p>
  </Modal>
</template>

<style scoped>
.bar { position: sticky; top: 53px; z-index: 6; display: flex; flex-direction: column; gap: 8px; margin-bottom: 8px; }
.gen, .sel { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.adv { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.adv input { width: 110px; }
.link { border: 0; background: none; color: var(--accent); }
.divider { flex: 1; }
.note { margin: 0 0 10px; }
.filters { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.filters .on { border-color: var(--accent); color: var(--accent); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 12px; }
article { cursor: pointer; transition: box-shadow 0.15s; user-select: none; }
article:hover { box-shadow: 0 2px 10px rgba(0, 0, 0, 0.08); }
article.running { border-color: var(--ok); }
article.on { box-shadow: 0 0 0 2px var(--accent); }
.ph { aspect-ratio: 1.2; background: var(--code); border-radius: 8px; }
.meta { display: flex; gap: 8px; align-items: baseline; margin-top: 8px; }
article p { margin: 2px 0 0; }
.pager { display: flex; gap: 10px; align-items: center; justify-content: center; margin-top: 14px; }
.pv-head { display: flex; gap: 8px; margin-bottom: 8px; }
@media (max-width: 860px) { .bar { top: 45px; } }
</style>
