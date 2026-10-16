<script setup lang="ts">
// 地点联想搜索：输入时自动出候选（国内百度、境外 OpenStreetMap），像地图应用一样（1.5）
import { onBeforeUnmount, ref, watch } from 'vue'
import { suggestPlaces } from '../../api'
import type { Place } from '../../types'
import Spinner from '../common/Spinner.vue'

const props = defineProps<{ modelValue: Place | null }>()
const emit = defineEmits<{ 'update:modelValue': [p: Place | null] }>()
const q = ref(props.modelValue?.name ?? '')
const items = ref<Place[]>([])
const loading = ref(false)
const open = ref(false)
let timer: number | undefined
let seq = 0
watch(q, (v) => {
  window.clearTimeout(timer)
  if (props.modelValue && v !== props.modelValue.name) emit('update:modelValue', null)
  if (v.trim().length < 2) { items.value = []; return }
  timer = window.setTimeout(async () => {
    const my = ++seq
    loading.value = true
    const r = await suggestPlaces(v.trim()).catch(() => [])
    if (my !== seq) return                    // 只用最后一次输入的结果
    items.value = r
    loading.value = false
    open.value = true
  }, 350)
})
function closeLater() { window.setTimeout(() => (open.value = false), 150) }
function pick(p: Place) { emit('update:modelValue', p); q.value = p.name; open.value = false }
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <div class="search">
    <input v-model="q" placeholder="搜索真实地点（可选），例如 千灯湖、Adelaide Botanic Garden" @focus="open = items.length > 0" @blur="closeLater" />
    <Spinner v-if="loading" class="spin" :size="12" />
    <ul v-if="open && items.length" class="drop card">
      <li v-for="p in items" :key="`${p.lat},${p.lon}`" @mousedown.prevent="pick(p)">
        <b>{{ p.name }}</b><span class="muted small">{{ p.address || p.city }} · {{ p.lat.toFixed(4) }}, {{ p.lon.toFixed(4) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.search { position: relative; flex: 1; min-width: 220px; }
input { width: 100%; }
.spin { position: absolute; right: 10px; top: 10px; color: var(--muted); }
.drop { position: absolute; top: 40px; left: 0; right: 0; z-index: 20; list-style: none; margin: 0; padding: 4px; max-height: 320px; overflow-y: auto; }
li { padding: 6px 8px; border-radius: 6px; cursor: pointer; display: flex; flex-direction: column; gap: 1px; }
li:hover { background: var(--code); }
</style>
