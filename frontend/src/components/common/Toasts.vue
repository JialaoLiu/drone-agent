<script setup lang="ts">
import { useToast } from '../../stores/toast'

const { toasts } = useToast()
</script>

<template>
  <div class="toasts" aria-live="polite">
    <TransitionGroup name="t">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="t.tone">{{ t.text }}</div>
    </TransitionGroup>
  </div>
</template>

<style scoped>
.toasts { position: fixed; right: 16px; bottom: 16px; z-index: 50; display: flex; flex-direction: column; gap: 8px; max-width: min(420px, calc(100vw - 32px)); }
.toast { padding: 10px 14px; border-radius: 10px; background: var(--panel); border: 1px solid var(--line); box-shadow: 0 6px 20px rgba(0, 0, 0, 0.15); font-size: 14px; border-left: 4px solid var(--accent); }
.toast.ok { border-left-color: var(--ok); }
.toast.warn { border-left-color: var(--warn); }
.toast.bad { border-left-color: var(--bad); }
.t-enter-active, .t-leave-active { transition: all 0.25s; }
.t-enter-from, .t-leave-to { opacity: 0; transform: translateY(10px); }
</style>
