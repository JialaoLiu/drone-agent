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

let gen = 0
watch(() => props.site.name, async (name) => {
  const my = ++gen                                    // 旧场地的底图参数晚到时丢弃，不能覆盖新场地（审查 M01）
  frame.value = null
  mode.value = real.value ? 'satellite' : 'plan'      // 真实场地默认显示卫星图（1.18、1.27）
  if (!props.site.location) return
  try {
    const f = await getBasemap(name)
    if (my === gen && name === props.site.name && f.available) frame.value = f as BaiduFrame
  } catch { /* 百度底图不可用时只提供 Esri 卫星图 */ }
}, { immediate: true })
const legend = computed(() => scenarioOf(props.site.scenario).legend)
// 国内 OpenStreetMap 的建筑轮廓常常缺失：卫星图上有楼、数据里没有时，这些楼不在仿真与安全校验里，要明说
const noBuildings = computed(() => {
  if (!real.value) return false
  const xs = props.site.geofence.map((p) => p[0]), ys = props.site.geofence.map((p) => p[1])
  return !(props.site.obstacles ?? []).some((o) => o.polygon && o.center[0] > Math.min(...xs) && o.center[0] < Math.max(...xs)
    && o.center[1] > Math.min(...ys) && o.center[1] < Math.max(...ys))
})
// 地标从哪来：真实场地只用真实存在的地物，没有时用方位描述（第三轮反馈：不能出现现场没有的凉亭）
const landmarkNote = computed(() => {
  const src = new Set((props.site.landmarks ?? []).map((l) => l.source))
  if (!real.value || !src.size) return ''
  const named = [src.has('osm') && 'OpenStreetMap', src.has('baidu') && '百度地点检索'].filter(Boolean).join('、')
  return (named ? `地标取自${named}中真实存在的地物` : '这里查不到可用的真实地标') + (src.has('direction') ? '；斜体的“场地北侧”等只是方位描述' : '')
})
</script>

<template>
  <div>
    <div class="switch">
      <button :class="{ on: mode === 'plan' }" class="small" @click="mode = 'plan'">平面图</button>
      <button :class="{ on: mode === 'satellite' }" class="small" :disabled="!real" :title="real ? '' : '随机生成的场地没有真实位置'" @click="mode = 'satellite'">卫星图</button>
      <button v-if="frame" :class="{ on: mode === 'baidu' }" class="small" @click="mode = 'baidu'">百度卫星图</button>
      <span v-if="real" class="muted small">布局依据：真实地物（OpenStreetMap 的水面、建筑轮廓、树）与高程；卫星图只作底图，不参与生成<template v-if="landmarkNote">。{{ landmarkNote }}</template></span>
      <span v-if="noBuildings" class="small warn-note">场地内没有建筑轮廓数据：如果卫星图上能看到楼房，说明 OpenStreetMap 在这里缺数据，这些楼房不在仿真和安全校验里</span>
    </div>
    <BaiduMap v-if="mode === 'baidu' && frame" :key="site.name" :site="site" :frame="frame" :findings="findings" :plan="plan" :telemetry="telemetry" />
    <SiteMap v-else :site="site" :findings="findings" :plan="plan" :telemetry="telemetry" :truth="truth" :score="score"
             :selected="selected" :satellite="mode === 'satellite'" :markers="markers" @select="(id) => emit('select', id)" />
    <MapLegend :items="legend" :comparison="!!score && !!site.panels.length" />
  </div>
</template>

<style scoped>
.warn-note { color: var(--warn, #b7791f); flex-basis: 100%; }
.switch { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.switch .on { border-color: var(--accent); color: var(--accent); }
</style>
