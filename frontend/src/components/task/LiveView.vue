<script setup lang="ts">
// 无人机实时视角：执行中每 2 秒刷新一次机载相机画面（1.34）。只在任务执行中显示，结束后停止请求。
import { onBeforeUnmount, ref, watch } from 'vue'
import { liveViewUrl } from '../../api'

const props = defineProps<{ active: boolean }>()
const camera = ref<'wide' | 'zoom'>('wide')
const tick = ref(0)
const src = ref('')
const failed = ref(false)
let timer: number | undefined
function load() {
  if (document.hidden) return                                     // 页面在后台时不取图
  const img = new Image()
  const url = liveViewUrl(camera.value, ++tick.value)
  img.onload = () => { src.value = url; failed.value = false }   // 新画面加载好再替换，避免闪烁
  img.onerror = () => { failed.value = true }
  img.src = url
}
watch(() => props.active, (on) => {
  window.clearInterval(timer)
  if (on) { load(); timer = window.setInterval(load, 2000) }     // 每 2 秒一帧：取图要从相机桥传整帧原始画面，太频繁会拖慢仿真（DR-085）
}, { immediate: true })
onBeforeUnmount(() => window.clearInterval(timer))
</script>

<template>
  <div v-if="active" class="card live">
    <div class="head">
      <h2><span class="rec" />实时视角</h2>
      <div class="small">
        <button class="small" :class="{ on: camera === 'wide' }" @click="camera = 'wide'">广角</button>
        <button class="small" :class="{ on: camera === 'zoom' }" @click="camera = 'zoom'">变焦</button>
      </div>
    </div>
    <img v-if="src" :src="src" alt="机载相机实时画面" />
    <p v-else class="muted small">{{ failed ? '暂时拿不到相机画面（相机桥未就绪）' : '正在连接相机……' }}</p>
  </div>
</template>

<style scoped>
.live { margin-bottom: 14px; }
.head { display: flex; justify-content: space-between; align-items: center; }
.head h2 { display: flex; align-items: center; gap: 6px; }
.rec { width: 8px; height: 8px; border-radius: 50%; background: #ef4444; animation: blink 1.2s ease-in-out infinite; }
.on { border-color: var(--accent); color: var(--accent); }
img { width: 100%; border-radius: 8px; display: block; background: #000; }
@keyframes blink { 50% { opacity: 0.2; } }
</style>
