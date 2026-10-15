<script setup lang="ts">
import type { PreflightItem } from '../types'

defineProps<{ items: PreflightItem[] }>()
const mark = (ok: boolean | null) => (ok === true ? '通过' : ok === false ? '不通过' : '提示')
</script>

<template>
  <ul class="preflight">
    <li v-for="i in items" :key="i.key" :class="i.ok === true ? 'ok' : i.ok === false ? 'bad' : 'warn'">
      <span class="tag">{{ mark(i.ok) }}</span><b>{{ i.label }}</b><span class="detail">{{ i.detail }}</span>
    </li>
  </ul>
</template>

<style scoped>
.preflight { list-style: none; margin: 0; padding: 0; font-size: 13px; }
.preflight li { display: grid; grid-template-columns: 52px 72px 1fr; gap: 6px; align-items: baseline; padding: 4px 0; border-bottom: 1px solid var(--line); }
.preflight li:last-child { border-bottom: 0; }
.tag { font-size: 12px; text-align: center; border-radius: 6px; padding: 1px 0; }
.ok .tag { background: var(--ok-bg); color: var(--ok); }
.bad .tag { background: var(--bad-bg); color: var(--bad); }
.warn .tag { background: var(--warn-bg); color: var(--warn); }
.detail { color: var(--muted); }
</style>
