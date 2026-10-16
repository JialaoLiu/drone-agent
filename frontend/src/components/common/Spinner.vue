<script setup lang="ts">
defineProps<{ size?: number }>()
</script>

<template>
  <span class="spinner" :style="{ width: (size ?? 14) + 'px', height: (size ?? 14) + 'px' }" aria-hidden="true" />
</template>

<style scoped>
.spinner { display: inline-block; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: spin 0.8s linear infinite; vertical-align: -2px; opacity: 0.8; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
