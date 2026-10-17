<script setup lang="ts">
// 页签：只负责显示与切换，内容由父组件按 modelValue 渲染。badge 用于提示“有新内容”（例如结果出来了）
defineProps<{ items: { key: string; label: string; badge?: string | number | null }[]; modelValue: string }>()
const emit = defineEmits<{ 'update:modelValue': [key: string] }>()
</script>

<template>
  <nav class="tabs" role="tablist">
    <button v-for="t in items" :key="t.key" role="tab" :aria-selected="modelValue === t.key" class="tab" :class="{ on: modelValue === t.key }"
            @click="emit('update:modelValue', t.key)">
      {{ t.label }}<span v-if="t.badge != null && t.badge !== ''" class="badge">{{ t.badge }}</span>
    </button>
  </nav>
</template>

<style scoped>
.tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--line); margin-bottom: 16px; overflow-x: auto; scrollbar-width: none; }
.tab { border: 0; border-radius: 0; background: none; padding: 10px 14px; color: var(--muted); font-size: 14px; white-space: nowrap;
  border-bottom: 2px solid transparent; margin-bottom: -1px; }
.tab:hover:not(:disabled) { color: var(--text); border-color: transparent; }
.tab.on { color: var(--text); font-weight: 600; border-bottom-color: var(--accent); }
.badge { margin-left: 6px; font-size: 11px; padding: 0 6px; border-radius: 999px; background: var(--accent-soft); color: var(--accent); font-weight: 500; }
</style>
