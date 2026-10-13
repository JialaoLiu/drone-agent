<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { createScene, getSite, getTruth, listScenes, startSim, stopSim } from '../api'
import SceneThumb from '../components/SceneThumb.vue'
import SiteMap from '../components/SiteMap.vue'
import { useSim } from '../composables/sim'
import type { SceneInfo, Site, Truth } from '../types'

const { sim, refresh } = useSim()
const scenes = ref<SceneInfo[]>([])
const sites = ref<Record<string, Site>>({})
const seed = ref<number>(Math.floor(Math.random() * 900) + 100)
const busy = ref('')
const error = ref('')
const preview = ref<string | null>(null)
const truth = ref<Truth | null>(null)

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
  <p v-if="error" class="notice">{{ error }}</p>
  <p class="small muted">当前仿真：{{ sim.scene ?? '未启动' }} <button v-if="sim.scene" class="small" :disabled="!!sim.active_mission" @click="stop">停止仿真</button></p>

  <div class="cards">
    <article v-for="s in scenes" :key="s.name" class="card" :class="{ running: sim.scene === s.name, on: preview === s.name }">
      <SceneThumb v-if="sites[s.name]" :site="sites[s.name]" @click="preview = s.name; truth = null" />
      <div class="meta">
        <b>{{ s.name }}</b><span class="muted small">种子 {{ s.seed }}</span>
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
