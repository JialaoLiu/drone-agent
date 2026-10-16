<script setup lang="ts">
// 搜救结果：确认的目标（照片、位置、是否落水）与搜索中看到的人和物
import { computed } from 'vue'
import { photoUrl } from '../../api'
import type { Scenario } from '../../scenarios'

const props = defineProps<{ result: Record<string, any> | null; scenario: Scenario }>()
const emit = defineEmits<{ photo: [id: number] }>()
const dets = computed(() => (props.result?.detections ?? []) as any[])
const order: Record<string, number> = { target: 0, candidate: 1, rejected: 2, unsure: 3, bystander: 4, object: 5 }
const shown = computed(() => [...dets.value].sort((a, b) => (order[a.kind] ?? 9) - (order[b.kind] ?? 9)))
</script>

<template>
  <div class="card">
    <h2>搜救结果</h2>
    <div v-if="result?.found" class="found" :class="{ water: result.found.in_water }">
      <img :src="photoUrl(result.found.photo, false, 480)" alt="确认照片" @click="emit('photo', result.found.photo)" />
      <div>
        <b>{{ result.found.in_water ? '目标在水中' : '已找到目标' }}</b>
        <p>位置：东 {{ result.found.x.toFixed(1) }} 米、北 {{ result.found.y.toFixed(1) }} 米（相对起飞点）</p>
        <p class="small muted">{{ result.found.evidence }}</p>
      </div>
    </div>
    <p v-else-if="result" class="muted small">没有找到目标。</p>
    <h3 class="small muted">搜索中看到的人和物（{{ dets.length }}）</h3>
    <ul>
      <li v-for="d in shown" :key="d.id" :class="d.kind">
        <span class="tag">{{ scenario.findingTypes[d.kind] ?? d.kind }}</span>
        <b>{{ d.id }}</b>
        <span class="small muted">{{ [d.top, d.bottom].filter(Boolean).join(' / ') }}{{ d.in_water ? ' · 水中' : '' }} · {{ d.evidence }}</span>
        <button class="link small" @click="emit('photo', d.confirm?.photo ?? d.photo)">照片</button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.found { display: flex; gap: 12px; align-items: flex-start; padding: 10px; border-radius: 10px; background: var(--ok-bg); margin-bottom: 10px; }
.found.water { background: var(--bad-bg); }
.found img { width: 200px; border-radius: 8px; cursor: zoom-in; }
.found p { margin: 4px 0 0; }
ul { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; max-height: 320px; overflow-y: auto; }
li { display: flex; gap: 8px; align-items: baseline; font-size: 13px; padding: 4px 0; border-bottom: 1px solid var(--line); }
li.target .tag { background: var(--bad-bg); color: var(--bad); }
li.candidate .tag, li.rejected .tag { background: var(--warn-bg); color: var(--warn); }
.link { border: 0; background: none; color: var(--accent); margin-left: auto; padding: 0 4px; }
h3 { margin: 10px 0 6px; font-weight: 500; }
</style>
