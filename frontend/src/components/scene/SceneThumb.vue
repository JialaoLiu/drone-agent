<script setup lang="ts">
import { computed } from 'vue'
import type { Site } from '../../types'

// 场景缩略图：围栏、禁飞区、树、光伏阵列、起飞点
const props = defineProps<{ site: Site }>()
const box = computed(() => {
  const xs = props.site.geofence.map((p) => p[0]), ys = props.site.geofence.map((p) => p[1])
  return { x0: Math.min(...xs) - 4, x1: Math.max(...xs) + 4, y0: Math.min(...ys) - 4, y1: Math.max(...ys) + 4 }
})
const sx = (x: number) => x - box.value.x0
const sy = (y: number) => box.value.y1 - y
const pts = (l: number[][]) => l.map((p) => `${sx(p[0])},${sy(p[1])}`).join(' ')
</script>

<template>
  <svg :viewBox="`0 0 ${box.x1 - box.x0} ${box.y1 - box.y0}`" class="thumb">
    <polygon :points="pts(site.geofence)" fill="var(--map-ground)" stroke="var(--muted)" stroke-dasharray="2 2" stroke-width="0.6" />
    <circle v-for="z in site.no_fly_zones" :key="z.id" :cx="sx(z.center[0])" :cy="sy(z.center[1])" :r="z.radius" fill="var(--bad)" fill-opacity="0.2" stroke="var(--bad)" stroke-width="0.6" />
    <circle v-for="o in site.obstacles" :key="o.id" :cx="sx(o.center[0])" :cy="sy(o.center[1])" :r="o.radius" fill="#3f8f3f" fill-opacity="0.6" />
    <polygon v-for="p in site.panels" :key="p.id" :points="pts(p.corners)" fill="var(--map-panel)" />
    <circle :cx="sx(0)" :cy="sy(0)" r="2.2" fill="none" stroke="var(--text)" stroke-width="0.8" />
  </svg>
</template>

<style scoped>
.thumb { width: 100%; height: auto; display: block; border-radius: 8px; }
</style>
