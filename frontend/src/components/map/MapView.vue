<script setup lang="ts">
// 场地图外壳：切换平面图 / 卫星图（Esri，真实场地，全球）/ 百度卫星图（国内真实场地，可选），并显示图例。
import { computed, ref, watch } from 'vue'
import { getBasemap } from '../../api'
import { scenarioOf } from '../../scenarios'
import type { Finding, Marker, Plan, Site, Telemetry, Truth } from '../../types'
import BaiduMap, { type BaiduFrame } from './BaiduMap.vue'
import MapLegend from './MapLegend.vue'
import SiteMap from './SiteMap.vue'

const props = defineProps<{
  site: Site; findings: Finding[]; plan: Plan | null; telemetry: Partial<Telemetry>
  truth?: Truth | null; score?: Record<string, any> | null; selected?: string | null; markers?: Marker[]
}>()
const emit = defineEmits<{ select: [panelId: string] }>()
const real = computed(() => !!props.site.location && !!props.site.origin)
const mode = ref<'plan' | 'satellite' | 'baidu'>('plan')
const frame = ref<BaiduFrame | null>(null)

watch(() => props.site.name, async (name) => {
  frame.value = null
  mode.value = real.value ? 'satellite' : 'plan'      // 真实场地默认显示卫星图（1.18、1.27）
  if (!props.site.location) return
  try {
    const f = await getBasemap(name)
    if (f.available) frame.value = f as BaiduFrame
  } catch { /* 百度底图不可用时只提供 Esri 卫星图 */ }
}, { immediate: true })
const legend = computed(() => scenarioOf(props.site.scenario).legend)
</script>

<template>
  <div>
    <div class="switch">
      <button :class="{ on: mode === 'plan' }" class="small" @click="mode = 'plan'">平面图</button>
      <button :class="{ on: mode === 'satellite' }" class="small" :disabled="!real" :title="real ? '' : '随机生成的场地没有真实位置'" @click="mode = 'satellite'">卫星图</button>
      <button v-if="frame" :class="{ on: mode === 'baidu' }" class="small" @click="mode = 'baidu'">百度卫星图</button>
      <span v-if="real" class="muted small">布局依据：真实地物（OpenStreetMap 的水面、建筑、树）与高程；卫星图只作底图，不参与生成</span>
    </div>
    <BaiduMap v-if="mode === 'baidu' && frame" :site="site" :frame="frame" :findings="findings" :plan="plan" :telemetry="telemetry" />
    <SiteMap v-else :site="site" :findings="findings" :plan="plan" :telemetry="telemetry" :truth="truth" :score="score"
             :selected="selected" :satellite="mode === 'satellite'" :markers="markers" @select="(id) => emit('select', id)" />
    <MapLegend :items="legend" :comparison="!!score && !!site.panels.length" />
  </div>
</template>

<style scoped>
.switch { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.switch .on { border-color: var(--accent); color: var(--accent); }
</style>
