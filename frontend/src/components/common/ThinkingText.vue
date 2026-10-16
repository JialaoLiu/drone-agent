<script setup lang="ts">
// 进行中的文字：从左到右的高光扫过，表示系统正在处理（1.14）
defineProps<{ text: string }>()
</script>

<template>
  <span class="thinking">{{ text }}</span>
</template>

<style scoped>
.thinking {
  background: linear-gradient(90deg, var(--muted) 0%, var(--muted) 40%, var(--text) 50%, var(--muted) 60%, var(--muted) 100%);
  background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
  animation: sweep 1.8s linear infinite;
}
@keyframes sweep { from { background-position: 100% 0; } to { background-position: -150% 0; } }
@media (prefers-reduced-motion: reduce) { .thinking { animation: none; color: var(--muted); } }
</style>
