<script setup lang="ts">
import { computed, ref } from 'vue'
import { photoUrl } from '../../api'
import type { Photo } from '../../types'

const props = defineProps<{ photos: Photo[]; open: number | null }>()
const emit = defineEmits<{ open: [photoId: number | null] }>()
const overlay = ref(true)

const current = computed(() => props.photos.find((p) => p.id === props.open) ?? null)
const KIND: Record<string, (t: string) => string> = {
  overview: (t) => `${t} 列整列`, segment: (t) => `${t} 附近分段`, closeup: (t) => `${t} 近照`, search: (t) => `搜索点 ${t}`, confirm: (t) => `确认 ${t}`,
}
const label = (p: Photo) => (KIND[p.kind] ?? ((t: string) => t))(p.target)
const screening = (p: Photo) => p.kind === 'overview' || p.kind === 'segment'   // 光伏初筛用的照片：叠加各板的判断框
const recent = computed(() => [...props.photos].reverse().slice(0, 24))
</script>

<template>
  <div class="card">
    <h2>照片 <span class="muted small">共 {{ photos.length }} 张</span></h2>
    <div v-if="photos.length" class="strip">
      <button v-for="p in recent" :key="p.id" class="thumb" :class="p.kind" @click="emit('open', p.id)">
        <img :src="photoUrl(p.id, screening(p), 240)" :alt="label(p)" loading="lazy" />
        <span>{{ label(p) }}</span>
      </button>
    </div>
    <p v-else class="muted small">飞行中拍摄的照片会实时出现在这里。</p>

    <div v-if="current" class="viewer" @click.self="emit('open', null)">
      <div class="frame">
        <div class="bar">
          <b>{{ label(current) }}</b>
          <span class="muted small">{{ current.camera === 'zoom' ? '变焦相机' : '广角相机' }} · 电量 {{ current.meta.battery }}%</span>
          <label class="small"><input v-model="overlay" type="checkbox" /> 标出光伏板</label>
          <button @click="emit('open', null)">关闭</button>
        </div>
        <img :src="photoUrl(current.id, overlay, 1280)" :alt="label(current)" />
        <p class="muted small">框的颜色：红色确认缺陷，橙色疑似，蓝色复核后排除，绿色正常。位置由相机位姿投影计算。</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.strip { display: flex; gap: 8px; overflow-x: auto; padding-bottom: 6px; }
.thumb { flex: 0 0 auto; padding: 0; border-radius: 8px; overflow: hidden; display: flex; flex-direction: column; background: var(--code); }
.thumb img { width: 160px; height: 90px; object-fit: cover; display: block; }
.thumb.closeup img { width: 90px; }
.thumb span { font-size: 11px; padding: 3px 6px; color: var(--muted); }
.viewer { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.6); display: flex; align-items: center; justify-content: center; z-index: 20; padding: 16px; }
.frame { background: var(--panel); border-radius: 12px; padding: 12px; max-width: 1100px; width: 100%; }
.frame img { width: 100%; border-radius: 8px; display: block; }
.bar { display: flex; align-items: center; gap: 12px; margin-bottom: 8px; flex-wrap: wrap; }
.bar button { margin-left: auto; }
</style>
