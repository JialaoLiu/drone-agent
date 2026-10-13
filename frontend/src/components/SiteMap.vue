<script setup lang="ts">
import { computed } from 'vue'
import type { Finding, Plan, Site, Telemetry, Truth } from '../types'

const props = defineProps<{
  site: Site
  findings: Finding[]
  plan: Plan | null
  telemetry: Partial<Telemetry>
  truth: Truth | null
  selected: string | null
}>()
const emit = defineEmits<{ select: [panelId: string] }>()

const PAD = 6
const bounds = computed(() => {
  const xs = props.site.geofence.map((p) => p[0])
  const ys = props.site.geofence.map((p) => p[1])
  return { minX: Math.min(...xs) - PAD, maxX: Math.max(...xs) + PAD, minY: Math.min(...ys) - PAD, maxY: Math.max(...ys) + PAD }
})
const SCALE = 8
const width = computed(() => (bounds.value.maxX - bounds.value.minX) * SCALE)
const height = computed(() => (bounds.value.maxY - bounds.value.minY) * SCALE)
// 本地坐标（东、北）到画布坐标：北朝上
const sx = (x: number) => (x - bounds.value.minX) * SCALE
const sy = (y: number) => (bounds.value.maxY - y) * SCALE
const pts = (list: number[][]) => list.map((p) => `${sx(p[0])},${sy(p[1])}`).join(' ')

const status = computed(() => Object.fromEntries(props.findings.map((f) => [f.panel_id, f.status])))
const truthIds = computed(() => new Set(props.truth?.defects.map((d) => d.panel_id) ?? []))
const fill = (id: string) => ({ confirmed: 'var(--bad)', suspect: '#e3a008', unverified: '#e3a008', cleared: '#5b8def' } as Record<string, string>)[status.value[id]] ?? 'var(--map-panel)'

const sortieColors = ['var(--map-route)', '#9b59d0', '#16a085']
const sorties = computed(() => {
  const route = props.plan?.route ?? []
  return (props.plan?.sorties ?? []).map((targets, i) => {
    // 新计划带每个架次的完整航线（含绕开禁飞区的绕行点）；旧计划只有拍摄点
    const path = props.plan?.routes?.[i] ?? route.filter((s) => targets.includes(s.target))
    const stops = path.filter((s) => s.kind === 'overview')
    const line = [[0, 0], ...path.map((s) => [s.position[0], s.position[1]]), [0, 0]]
    return { color: sortieColors[i % sortieColors.length], line, stops, detours: path.filter((s) => s.kind === 'transit') }
  })
})
const drone = computed(() => (props.telemetry && props.telemetry.updated ? props.telemetry : null))
</script>

<template>
  <svg :viewBox="`0 0 ${width} ${height}`" class="map" role="img" aria-label="场地平面图">
    <rect :width="width" :height="height" fill="var(--map-ground)" />
    <polygon :points="pts(site.geofence)" fill="none" stroke="var(--muted)" stroke-dasharray="6 5" stroke-width="2" />
    <text :x="sx(site.geofence[0][0]) + 6" :y="sy(site.geofence[0][1]) - 6" class="label">电子围栏 · 限高 {{ site.max_altitude }} 米</text>

    <g v-for="z in site.no_fly_zones" :key="z.id">
      <circle :cx="sx(z.center[0])" :cy="sy(z.center[1])" :r="z.radius * SCALE" fill="var(--bad)" fill-opacity="0.15" stroke="var(--bad)" stroke-width="2" />
      <text :x="sx(z.center[0])" :y="sy(z.center[1])" class="label center">{{ z.label }}（禁飞）</text>
    </g>
    <g v-for="o in site.obstacles" :key="o.id">
      <circle :cx="sx(o.center[0])" :cy="sy(o.center[1])" :r="o.radius * SCALE" fill="#3f8f3f" fill-opacity="0.55" />
      <text :x="sx(o.center[0])" :y="sy(o.center[1]) + o.radius * SCALE + 12" class="label center small">{{ o.label }} {{ o.height }} 米</text>
    </g>

    <g v-for="a in site.arrays" :key="a.id">
      <text :x="sx(a.center[0] - a.size[0] / 2) - 14" :y="sy(a.center[1]) + 5" class="label strong">{{ a.id }}</text>
    </g>
    <polygon v-for="p in site.panels" :key="p.id" :points="pts(p.corners)" :fill="fill(p.id)"
             :stroke="selected === p.id ? 'var(--accent)' : truthIds.has(p.id) ? '#d6336c' : 'rgba(255,255,255,0.5)'"
             :stroke-width="selected === p.id || truthIds.has(p.id) ? 3 : 0.8" :stroke-dasharray="truthIds.has(p.id) && selected !== p.id ? '4 3' : undefined"
             class="panel" @click="emit('select', p.id)">
      <title>{{ p.id }}</title>
    </polygon>

    <g v-for="(s, i) in sorties" :key="i">
      <polyline :points="pts(s.line)" fill="none" :stroke="s.color" stroke-width="2.5" stroke-dasharray="8 6" opacity="0.9" />
      <circle v-for="(d, j) in s.detours" :key="'d' + j" :cx="sx(d.position[0])" :cy="sy(d.position[1])" r="4" fill="none" :stroke="s.color" stroke-width="2" />
      <g v-for="v in s.stops" :key="v.target">
        <circle :cx="sx(v.position[0])" :cy="sy(v.position[1])" r="7" :fill="s.color" />
        <text :x="sx(v.position[0]) + 10" :y="sy(v.position[1]) + 4" class="label small">{{ v.target }}</text>
      </g>
    </g>

    <g :transform="`translate(${sx(0)},${sy(0)})`">
      <circle r="11" fill="none" stroke="var(--text)" stroke-width="2" />
      <text class="label center strong" y="5">H</text>
    </g>
    <g v-if="drone" :transform="`translate(${sx(drone.x ?? 0)},${sy(drone.y ?? 0)}) rotate(${drone.yaw ?? 0})`">
      <polygon points="0,-14 9,10 0,5 -9,10" fill="var(--accent)" stroke="#fff" stroke-width="1.5" />
    </g>
  </svg>
</template>

<style scoped>
.map { width: 100%; height: auto; display: block; border-radius: 10px; }
.label { font-size: 13px; fill: var(--text); }
.label.small { font-size: 11px; fill: var(--muted); }
.label.center { text-anchor: middle; }
.label.strong { font-weight: 700; font-size: 15px; }
.panel { cursor: pointer; }
</style>
