<script setup lang="ts">
// 百度卫星底图（只用于国内的真实场地）。场地内部坐标是 WGS-84 本地平面（x 东、y 北，米），
// 所有要素都经同一个函数 toBd() 换算为 BD-09 再画到百度地图上，换算参数由后端 /site/{name}/basemap 给出。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Finding, Plan, Site, Telemetry } from '../types'

export interface BaiduFrame {
  available: boolean; ak: string; origin_bd09: number[]
  lat_per_e: number; lon_per_e: number; lat_per_n: number; lon_per_n: number; max_error_m: number
}

const props = defineProps<{ site: Site; frame: BaiduFrame; findings: Finding[]; plan: Plan | null; telemetry: Partial<Telemetry> }>()
const el = ref<HTMLDivElement | null>(null)
const error = ref('')
let map: any = null
let droneMarker: any = null
const overlays: any[] = []

declare global { interface Window { BMapGL?: any; __baiduReady?: Promise<void>; __baiduInit?: () => void } }

// 本地坐标（米）→ BD-09 经纬度（全部要素共用）
function toBd(x: number, y: number) {
  const f = props.frame
  const lat = f.origin_bd09[0] + f.lat_per_e * x + f.lat_per_n * y
  const lng = f.origin_bd09[1] + f.lon_per_e * x + f.lon_per_n * y
  return new window.BMapGL.Point(lng, lat)
}

function loadScript(ak: string): Promise<void> {
  if (window.__baiduReady) return window.__baiduReady
  window.__baiduReady = new Promise((resolve, reject) => {
    window.__baiduInit = () => resolve()
    const s = document.createElement('script')
    s.src = `https://api.map.baidu.com/api?type=webgl&v=1.0&ak=${encodeURIComponent(ak)}&callback=__baiduInit`
    s.onerror = () => reject(new Error('百度地图脚本加载失败（网络或 AK / Referer 白名单）'))
    document.head.appendChild(s)
  })
  return window.__baiduReady
}

const status = computed(() => Object.fromEntries(props.findings.map((f) => [f.panel_id, f.status])))
const panelColor = (id: string) => ({ confirmed: '#e03131', suspect: '#e3a008', unverified: '#e3a008', cleared: '#5b8def' } as Record<string, string>)[status.value[id]] ?? '#1c2f6b'

function draw() {
  if (!map) return
  overlays.splice(0).forEach((o) => map.removeOverlay(o))
  const B = window.BMapGL
  const add = (o: any) => { map.addOverlay(o); overlays.push(o) }
  const s = props.site
  add(new B.Polygon(s.geofence.map((p) => toBd(p[0], p[1])), { strokeColor: '#ffffff', strokeWeight: 2, strokeStyle: 'dashed', fillOpacity: 0.01 }))
  for (const z of s.no_fly_zones) add(new B.Circle(toBd(z.center[0], z.center[1]), z.radius, { strokeColor: '#e03131', fillColor: '#e03131', fillOpacity: 0.25, strokeWeight: 2 }))
  for (const o of s.obstacles) add(new B.Circle(toBd(o.center[0], o.center[1]), o.radius, { strokeColor: '#2f9e44', fillColor: '#2f9e44', fillOpacity: 0.5, strokeWeight: 1 }))
  for (const p of s.panels) add(new B.Polygon(p.corners.map((c) => toBd(c[0], c[1])), { strokeColor: '#ffffff', strokeWeight: 0.5, fillColor: panelColor(p.id), fillOpacity: 0.9 }))
  const colors = ['#4dabf7', '#b197fc', '#38d9a9']
  ;(props.plan?.sorties ?? []).forEach((targets, i) => {
    const path = props.plan?.routes?.[i] ?? (props.plan?.route ?? []).filter((st) => targets.includes(st.target))
    const line = [[0, 0], ...path.map((st) => [st.position[0], st.position[1]]), [0, 0]]
    add(new B.Polyline(line.map((p) => toBd(p[0], p[1])), { strokeColor: colors[i % colors.length], strokeWeight: 3, strokeStyle: 'dashed' }))
    for (const d of path.filter((st) => st.kind === 'transit')) add(new B.Circle(toBd(d.position[0], d.position[1]), 1.5, { strokeColor: colors[i % colors.length], fillOpacity: 0, strokeWeight: 2 }))
  })
  add(new B.Marker(toBd(0, 0), { title: '起飞点' }))
  updateDrone()
}

function updateDrone() {
  if (!map) return
  const t = props.telemetry
  if (droneMarker) { map.removeOverlay(droneMarker); droneMarker = null }
  if (t && t.updated) {
    droneMarker = new window.BMapGL.Circle(toBd(t.x ?? 0, t.y ?? 0), 2, { strokeColor: '#ffffff', fillColor: '#228be6', fillOpacity: 1, strokeWeight: 2 })
    map.addOverlay(droneMarker)
  }
}

onMounted(async () => {
  try {
    await loadScript(props.frame.ak)
    const B = window.BMapGL
    map = new B.Map(el.value)
    const c = toBd(0, 0)
    map.centerAndZoom(c, 18)
    map.enableScrollWheelZoom(true)
    draw()
    // 卫星图（BMAP_SATELLITE_MAP）：地图第一次加载完成后再切换，初始化过程中切换会抛错并中断绘制
    let switched = false
    map.addEventListener('tilesloaded', () => {
      if (switched || !map) return
      switched = true
      map.setMapType((window as any).BMAP_SATELLITE_MAP)
      window.setTimeout(() => map?.centerAndZoom(toBd(0, 0), 19), 300)     // 切换类型后缩放级别会被重置
    })
  } catch (e) {
    error.value = (e as Error).message
  }
})
watch(() => [props.findings, props.plan], draw, { deep: true })
watch(() => props.telemetry, updateDrone)
onBeforeUnmount(() => { if (map) { map.destroy?.(); map = null } })
</script>

<template>
  <div class="wrap">
    <div ref="el" class="bmap" />
    <p v-if="error" class="notice small">{{ error }}</p>
    <p class="small muted note">百度卫星底图（BD-09）。场地内部为 WGS-84，全部要素统一换算：换算公式误差约 1–2 米，线性近似另有约 {{ frame.max_error_m.toFixed(2) }} 米。</p>
  </div>
</template>

<style scoped>
.bmap { width: 100%; height: 560px; border-radius: 10px; overflow: hidden; background: #1b1f24; }
.note { margin: 6px 0 0; }
</style>
