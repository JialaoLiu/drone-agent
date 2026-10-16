<script setup lang="ts">
// 场地图：默认平面图；国内真实场地可切换百度卫星底图（参数由后端给出，不可用时说明原因）
import { ref, watch } from 'vue'
import { getBasemap } from '../api'
import type { Finding, Plan, Site, Telemetry, Truth } from '../types'
import BaiduMap, { type BaiduFrame } from './BaiduMap.vue'
import SiteMap from './SiteMap.vue'

const props = defineProps<{ site: Site; findings: Finding[]; plan: Plan | null; telemetry: Partial<Telemetry>; truth: Truth | null; selected: string | null }>()
const emit = defineEmits<{ select: [panelId: string] }>()
const mode = ref<'plan' | 'baidu'>('plan')
const frame = ref<BaiduFrame | null>(null)
const reason = ref('')

watch(() => props.site.name, async (name) => {
  mode.value = 'plan'
  frame.value = null
  reason.value = ''
  if (!props.site.location) return
  try {
    const f = await getBasemap(name)
    if (f.available) frame.value = f as BaiduFrame
    else reason.value = f.reason ?? ''
  } catch (e) { reason.value = (e as Error).message }
}, { immediate: true })
</script>

<template>
  <div>
    <div v-if="frame || reason" class="switch small">
      <button :class="{ on: mode === 'plan' }" class="small" @click="mode = 'plan'">平面图</button>
      <button :class="{ on: mode === 'baidu' }" class="small" :disabled="!frame" :title="reason" @click="mode = 'baidu'">百度卫星图</button>
      <span v-if="!frame && reason" class="muted">{{ reason }}</span>
    </div>
    <BaiduMap v-if="mode === 'baidu' && frame" :site="site" :frame="frame" :findings="findings" :plan="plan" :telemetry="telemetry" />
    <SiteMap v-else :site="site" :findings="findings" :plan="plan" :telemetry="telemetry" :truth="truth" :selected="selected" @select="(id) => emit('select', id)" />
  </div>
</template>

<style scoped>
.switch { display: flex; gap: 6px; align-items: center; margin-bottom: 8px; flex-wrap: wrap; }
.switch .on { border-color: var(--accent); color: var(--accent); }
</style>
