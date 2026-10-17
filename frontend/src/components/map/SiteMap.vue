<script setup lang="ts">
// 场地图：平面图与卫星底图共用一个组件（卫星瓦片与场地同为 WGS-84，直接对齐）。
// 只负责展示：场地、发现、计划航线、飞机位置、标准答案对照；点击板块时发出 select。
import { computed } from 'vue'
import { IMAGERY_ATTRIBUTION, tilesFor } from '../../geo/tiles'
import { useTweenedPose } from '../../stores/tween'
import type { Finding, Marker, Plan, Site, Telemetry, Truth } from '../../types'

const props = defineProps<{
  site: Site; findings: Finding[]; plan: Plan | null; telemetry: Partial<Telemetry>
  truth?: Truth | null; score?: Record<string, any> | null; selected?: string | null; satellite?: boolean; markers?: Marker[]
}>()
const emit = defineEmits<{ select: [panelId: string] }>()

const PAD = 8
const SCALE = 8
const bounds = computed(() => {
  const xs = props.site.geofence.map((p) => p[0]), ys = props.site.geofence.map((p) => p[1])
  return { minX: Math.min(...xs) - PAD, maxX: Math.max(...xs) + PAD, minY: Math.min(...ys) - PAD, maxY: Math.max(...ys) + PAD }
})
const width = computed(() => (bounds.value.maxX - bounds.value.minX) * SCALE)
const height = computed(() => (bounds.value.maxY - bounds.value.minY) * SCALE)
const sx = (x: number) => (x - bounds.value.minX) * SCALE
const sy = (y: number) => (bounds.value.maxY - y) * SCALE        // 北朝上
const pts = (list: number[][]) => list.map((p) => `${sx(p[0])},${sy(p[1])}`).join(' ')

const tiles = computed(() => {
  const o = props.site.origin
  if (!props.satellite || !o) return []
  const b = bounds.value
  return tilesFor(o, b.minX, b.maxX, b.minY, b.maxY, 19)
})

const status = computed(() => Object.fromEntries(props.findings.map((f) => [f.panel_id, f.status])))
const fill = (id: string) => ({ confirmed: 'var(--bad)', suspect: '#e3a008', unverified: '#e3a008', cleared: '#5b8def' } as Record<string, string>)[status.value[id]] ?? 'var(--map-panel)'
const truthIds = computed(() => new Set(props.truth?.defects.map((d) => d.panel_id) ?? []))
// 对照标准答案：漏检、误报、类型错分别标出（1.36）
const missed = computed(() => new Set<string>(props.score?.missed ?? []))
const falseAlarms = computed(() => new Set<string>(props.score?.false_alarms ?? []))
const wrongType = computed(() => new Set<string>((props.score?.wrong_type ?? []).map((w: any) => w.panel)))
const mark = (id: string) => (missed.value.has(id) ? 'missed' : falseAlarms.value.has(id) ? 'false' : wrongType.value.has(id) ? 'wrong' : '')

const sortieColors = ['var(--map-route)', '#9b59d0', '#16a085']
const sorties = computed(() => (props.plan?.sorties ?? []).map((targets, i) => {
  const path = props.plan?.routes?.[i] ?? (props.plan?.route ?? []).filter((s) => targets.includes(s.target))
  return {
    color: sortieColors[i % sortieColors.length], line: [[0, 0], ...path.map((s) => [s.position[0], s.position[1]]), [0, 0]],
    stops: path.filter((s) => s.kind === 'overview'), detours: path.filter((s) => s.kind === 'transit'),
  }
}))
const FEATURE: Record<string, { fill: string; stroke: string; opacity: number }> = {
  water: { fill: '#4dabf7', stroke: '#1c7ed6', opacity: 0.35 }, building: { fill: '#868e96', stroke: '#495057', opacity: 0.5 },
  wood: { fill: '#2f9e44', stroke: '#2b8a3e', opacity: 0.2 }, solar: { fill: 'none', stroke: '#f08c00', opacity: 1 },
  aeroway: { fill: '#fa5252', stroke: '#e03131', opacity: 0.2 },
}
const MARK: Record<string, string> = { target: '#e03131', bad: '#e03131', warn: '#f59f00', muted: '#868e96', info: '#7048e8' }
const source = computed(() => (props.telemetry?.updated ? { x: props.telemetry.x ?? 0, y: props.telemetry.y ?? 0, yaw: props.telemetry.yaw ?? 0 } : null))
const drone = useTweenedPose(source)
</script>

<template>
  <div class="wrap">
    <svg :viewBox="`0 0 ${width} ${height}`" class="map" :class="{ sat: tiles.length }" role="img" aria-label="场地图">
      <rect :width="width" :height="height" fill="var(--map-ground)" />
      <image v-for="t in tiles" :key="t.url" :href="t.url" :x="sx(t.x)" :y="sy(t.y)" :width="t.size * SCALE" :height="t.size * SCALE" preserveAspectRatio="none" />
      <polygon v-for="(f, i) in site.features ?? []" :key="'f' + i" :points="pts(f.polygon)" :fill="FEATURE[f.kind]?.fill"
        :fill-opacity="tiles.length ? 0.08 : FEATURE[f.kind]?.opacity" :stroke="FEATURE[f.kind]?.stroke" stroke-width="1.5"><title>{{ f.kind }}</title></polygon>
      <polygon :points="pts(site.geofence)" fill="none" :stroke="tiles.length ? '#fff' : 'var(--muted)'" stroke-dasharray="6 5" stroke-width="2" />
      <text :x="sx(site.geofence[0][0]) + 6" :y="sy(site.geofence[0][1]) - 6" class="label">电子围栏 · 限高 {{ site.max_altitude }} 米</text>

      <g v-for="z in site.no_fly_zones" :key="z.id">
        <circle :cx="sx(z.center[0])" :cy="sy(z.center[1])" :r="z.radius * SCALE" fill="var(--bad)" fill-opacity="0.18" stroke="var(--bad)" stroke-width="2" />
        <text :x="sx(z.center[0])" :y="sy(z.center[1])" class="label center">{{ z.label }}（禁飞）</text>
      </g>
      <g v-for="o in site.obstacles" :key="o.id">
        <circle :cx="sx(o.center[0])" :cy="sy(o.center[1])" :r="o.radius * SCALE" fill="#3f8f3f" fill-opacity="0.55" />
        <text :x="sx(o.center[0])" :y="sy(o.center[1]) + o.radius * SCALE + 12" class="label center small">{{ o.label }} {{ o.height }} 米</text>
      </g>
      <text v-for="a in site.arrays" :key="a.id" :x="sx(a.center[0] - a.size[0] / 2) - 14" :y="sy(a.center[1]) + 5" class="label strong">{{ a.id }}</text>
      <polygon v-for="p in site.panels" :key="p.id" :points="pts(p.corners)" :fill="fill(p.id)" class="panel"
        :class="[mark(p.id), { on: selected === p.id, truth: truthIds.has(p.id) }]" @click="emit('select', p.id)"><title>{{ p.id }}</title></polygon>

      <g v-for="(s, i) in sorties" :key="i">
        <polyline :points="pts(s.line)" fill="none" :stroke="s.color" stroke-width="2.5" stroke-dasharray="8 6" opacity="0.9" />
        <circle v-for="(d, j) in s.detours" :key="'d' + j" :cx="sx(d.position[0])" :cy="sy(d.position[1])" r="4" fill="none" :stroke="s.color" stroke-width="2" />
        <g v-for="v in s.stops" :key="v.target">
          <circle :cx="sx(v.position[0])" :cy="sy(v.position[1])" r="7" :fill="s.color" />
          <text :x="sx(v.position[0]) + 10" :y="sy(v.position[1]) + 4" class="label small">{{ v.target }}</text>
        </g>
      </g>
      <g v-for="l in site.landmarks ?? []" :key="l.id">
        <circle :cx="sx(l.center[0])" :cy="sy(l.center[1])" r="6" fill="#7048e8" stroke="#fff" stroke-width="1.5" />
        <text :x="sx(l.center[0]) + 9" :y="sy(l.center[1]) + 4" class="label">{{ l.label }}</text>
      </g>
      <g v-for="p in truth?.people ?? []" :key="p.id">
        <circle :cx="sx(p.start[0])" :cy="sy(p.start[1])" :r="p.role === 'target' ? 7 : 5" fill="none" :stroke="p.role === 'target' ? '#d6336c' : '#adb5bd'" stroke-width="2" stroke-dasharray="3 2" />
        <text v-if="p.role === 'target'" :x="sx(p.start[0]) + 9" :y="sy(p.start[1]) - 6" class="label small">目标起点（{{ p.state === 'water' ? '落水' : p.state === 'walking' ? '走动' : '静止' }}）</text>
      </g>
      <g :transform="`translate(${sx(0)},${sy(0)})`">
        <circle r="11" fill="var(--panel)" fill-opacity="0.7" stroke="var(--text)" stroke-width="2" />
        <text class="label center strong" y="5">H</text>
      </g>
    </svg>
    <!-- 会动的东西（飞机、发现的人）单独一层：飞机移动时不重绘带卫星影像的底图（DR-085） -->
    <svg :viewBox="`0 0 ${width} ${height}`" class="map overlay" aria-hidden="true">
      <g v-for="(m, i) in markers ?? []" :key="'m' + i" class="marker">
        <circle :cx="sx(m.x)" :cy="sy(m.y)" :r="m.tone === 'target' ? 9 : 6" :fill="MARK[m.tone]" fill-opacity="0.85" stroke="#fff" stroke-width="2" />
        <text v-if="m.tone === 'target' || m.tone === 'warn'" :x="sx(m.x) + 11" :y="sy(m.y) + 4" class="label small">{{ m.label }}</text>
      </g>
      <g v-if="drone" :transform="`translate(${sx(drone.x)},${sy(drone.y)}) rotate(${drone.yaw})`" class="drone">
        <circle r="18" fill="var(--accent)" fill-opacity="0.15" />
        <polygon points="0,-14 9,10 0,5 -9,10" fill="var(--accent)" stroke="#fff" stroke-width="1.5" />
      </g>
    </svg>
    <p v-if="tiles.length" class="attribution">{{ IMAGERY_ATTRIBUTION }}</p>
  </div>
</template>

<style scoped>
.wrap { position: relative; }
.map { width: 100%; height: auto; display: block; border-radius: 10px; }
.overlay { position: absolute; inset: 0; pointer-events: none; }
.drone { will-change: transform; }
.label { font-size: 13px; fill: var(--text); paint-order: stroke; stroke: var(--panel); stroke-width: 3px; }
.sat .label { fill: #fff; stroke: rgba(0, 0, 0, 0.6); }
.label.small { font-size: 11px; }
.label.center { text-anchor: middle; }
.label.strong { font-weight: 700; font-size: 15px; }
.panel { cursor: pointer; stroke: rgba(255, 255, 255, 0.5); stroke-width: 0.8; transition: fill 0.4s; }
.panel.truth { stroke: #d6336c; stroke-width: 3; stroke-dasharray: 4 3; }
.panel.missed { stroke: #d6336c; stroke-width: 4; stroke-dasharray: none; animation: pulse 1.6s ease-in-out infinite; }
.panel.false { stroke: #f59f00; stroke-width: 4; }
.panel.wrong { stroke: #7048e8; stroke-width: 4; }
.panel.on { stroke: var(--accent); stroke-width: 4; stroke-dasharray: none; }
.attribution { position: absolute; right: 8px; bottom: 6px; margin: 0; font-size: 10px; color: #fff; text-shadow: 0 0 3px #000; }
@keyframes pulse { 50% { stroke-opacity: 0.25; } }
</style>
