<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { createScene, getSite, getTruth, listScenes, searchPlaces, startSim, stopSim } from '../api'
import SceneThumb from '../components/SceneThumb.vue'
import SiteMap from '../components/SiteMap.vue'
import { useSim } from '../composables/sim'
import type { Place, SceneInfo, Site, Truth } from '../types'

const { sim, refresh } = useSim()
const scenes = ref<SceneInfo[]>([])
const sites = ref<Record<string, Site>>({})
const seed = ref<number>(Math.floor(Math.random() * 900) + 100)
const busy = ref('')
const error = ref('')
const preview = ref<string | null>(null)
const truth = ref<Truth | null>(null)

// 真实场地：地名检索（百度），选中后按当前种子生成；阵列等仍随机布置
const placeQuery = ref('')
const placeCity = ref('')
const places = ref<Place[]>([])
const groundNote = ref('')

async function findPlaces() {
  if (!placeQuery.value.trim()) return
  busy.value = 'places'
  error.value = ''
  try { places.value = await searchPlaces(placeQuery.value.trim(), placeCity.value.trim() || '全国') }
  catch (e) { error.value = (e as Error).message } finally { busy.value = '' }
}

async function generateAt(place: Place) {
  busy.value = 'generate'
  error.value = ''
  try {
    const { name, ground } = await createScene(seed.value, place)
    groundNote.value = `${place.name}：${ground?.note ?? ''}`
    places.value = []
    await load()
    preview.value = name
  } catch (e) { error.value = (e as Error).message } finally { busy.value = '' }
}

const previewSite = computed(() => (preview.value ? sites.value[preview.value] : null))

async function load() {
  scenes.value = await listScenes()
  await Promise.all(scenes.value.map(async (s) => { if (!sites.value[s.name]) sites.value[s.name] = await getSite(s.name) }))
}

async function generate(random = false) {
  if (random) seed.value = Math.floor(Math.random() * 100000) + 1
  busy.value = 'generate'
  error.value = ''
  try {
    const { name } = await createScene(seed.value)
    await load()
    preview.value = name
  } catch (e) { error.value = (e as Error).message } finally { busy.value = '' }
}

async function start(name: string) {
  busy.value = name
  error.value = ''
  try { await startSim(name); await refresh() } catch (e) { error.value = (e as Error).message } finally { busy.value = '' }
}

async function stop() {
  try { await stopSim(); await refresh() } catch (e) { error.value = (e as Error).message }
}

async function showTruth(name: string) {
  truth.value = truth.value ? null : await getTruth(name)
}

const info = (name: string) => {
  const s = sites.value[name]
  if (!s) return ''
  return `${s.arrays.length} 列 · ${s.panels.length} 块板 · ${s.no_fly_zones.length} 个禁飞区 · ${s.obstacles.length} 棵树`
}
onMounted(load)
</script>

<template>
  <div class="page-head">
    <p>每个场景由随机种子生成：围栏形状、禁飞区、光伏阵列的数量 / 位置 / 朝向 / 行列数、树的位置、缺陷与干扰都随种子变化，同一种子完全可复现。</p>
    <div class="gen">
      <label class="small">种子 <input v-model.number="seed" type="number" min="1" max="100000" /></label>
      <button :disabled="!!busy" @click="generate()">生成</button>
      <button :disabled="!!busy" @click="generate(true)">随机生成一个</button>
    </div>
  </div>
  <section class="card place">
    <h2>真实场地</h2>
    <p class="small muted">输入地名（例如“文翰湖公园”），用百度地点检索取得坐标，场地原点放在真实位置，起飞天气按该位置判断；有 Mapbox Token 时地面换成该位置的卫星影像。光伏阵列、禁飞区、树仍按种子随机布置，与影像中的真实地物无关。</p>
    <div class="gen">
      <input v-model="placeQuery" placeholder="地名" @keydown.enter="findPlaces" />
      <input v-model="placeCity" placeholder="城市（可选，例如 佛山）" @keydown.enter="findPlaces" />
      <button :disabled="!!busy || !placeQuery.trim()" @click="findPlaces">查找</button>
    </div>
    <ul v-if="places.length" class="places">
      <li v-for="p in places" :key="`${p.lat},${p.lon}`">
        <span><b>{{ p.name }}</b> <span class="muted small">{{ p.city }} {{ p.address }} · {{ p.lat.toFixed(5) }}, {{ p.lon.toFixed(5) }}</span></span>
        <button class="small" :disabled="!!busy" @click="generateAt(p)">用种子 {{ seed }} 生成</button>
      </li>
    </ul>
    <p v-if="groundNote" class="small muted">{{ groundNote }}</p>
  </section>
  <p v-if="error" class="notice">{{ error }}</p>
  <p class="small muted">当前仿真：{{ sim.scene ?? '未启动' }} <button v-if="sim.scene" class="small" :disabled="!!sim.active_mission" @click="stop">停止仿真</button></p>

  <div class="cards">
    <article v-for="s in scenes" :key="s.name" class="card" :class="{ running: sim.scene === s.name, on: preview === s.name }">
      <SceneThumb v-if="sites[s.name]" :site="sites[s.name]" @click="preview = s.name; truth = null" />
      <div class="meta">
        <b>{{ s.name }}</b><span class="muted small">种子 {{ s.seed }}</span>
        <span v-if="s.location" class="tag">{{ s.location }}{{ s.ground === 'satellite' ? ' · 卫星地面' : '' }}</span>
        <span v-if="sim.scene === s.name" class="tag ok">运行中</span>
      </div>
      <p class="small muted">{{ info(s.name) }}</p>
      <div class="row">
        <button class="small" :disabled="!!busy || !!sim.active_mission" @click="start(s.name)">
          {{ busy === s.name ? '启动中…（约 30 秒）' : sim.scene === s.name ? '重启' : '启动仿真' }}
        </button>
        <button class="small" @click="preview = s.name; truth = null">查看</button>
      </div>
    </article>
  </div>

  <section v-if="previewSite" class="card gap">
    <div class="head">
      <h2>{{ previewSite.name }} 平面图</h2>
      <button class="small" @click="showTruth(previewSite.name)">{{ truth ? '隐藏标准答案' : '显示标准答案' }}</button>
    </div>
    <SiteMap :site="previewSite" :findings="[]" :plan="null" :telemetry="{}" :truth="truth" :selected="null" />
    <p v-if="truth" class="small muted">标准答案：{{ truth.defects.map((d) => `${d.panel_id} ${d.label}`).join('；') }}</p>
  </section>
</template>

<style scoped>
.place { margin-bottom: 14px; }
.place h2 { margin: 0 0 4px; font-size: 15px; }
.places { list-style: none; padding: 0; margin: 10px 0 0; display: grid; gap: 6px; }
.places li { display: flex; justify-content: space-between; align-items: center; gap: 10px; flex-wrap: wrap; border-top: 1px solid var(--line); padding-top: 6px; }
.gen { display: flex; gap: 8px; align-items: center; }
.gen input { width: 100px; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 12px; margin-top: 10px; }
article { cursor: default; }
article.running { border-color: var(--ok); }
article.on { box-shadow: 0 0 0 2px var(--accent); }
article :deep(svg) { cursor: pointer; }
.meta { display: flex; gap: 8px; align-items: baseline; margin-top: 8px; }
.row { display: flex; gap: 6px; }
.gap { margin-top: 14px; }
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.head h2 { margin: 0; }
.notice { padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); }
</style>
