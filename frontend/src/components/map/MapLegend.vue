<script setup lang="ts">
import type { LegendItem } from '../../scenarios'

defineProps<{ items: LegendItem[]; comparison?: boolean }>()
</script>

<template>
  <p class="legend small muted">
    <span v-for="i in items" :key="i.label"><i :class="i.shape ?? 'square'" :style="{ '--c': i.color }" />{{ i.label }}</span>
    <template v-if="comparison">
      <span><i class="ring" style="--c: #d6336c" />漏检（闪烁）</span>
      <span><i class="ring" style="--c: #f59f00" />误报</span>
      <span><i class="ring" style="--c: #7048e8" />类型错</span>
    </template>
  </p>
</template>

<style scoped>
.legend { display: flex; flex-wrap: wrap; gap: 4px 14px; align-items: center; margin: 8px 0 0; }
i { display: inline-block; margin-right: 5px; vertical-align: -1px; }
.square { width: 10px; height: 10px; border-radius: 2px; background: var(--c); }
.dash { width: 18px; border-top: 2px dashed var(--c); vertical-align: 3px; }
.circle { width: 10px; height: 10px; border-radius: 50%; background: var(--c); }
.ring { width: 10px; height: 10px; border-radius: 2px; border: 2px solid var(--c); }
</style>
