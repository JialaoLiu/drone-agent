<script setup lang="ts">
defineProps<{ title: string }>()
const emit = defineEmits<{ close: [] }>()
</script>

<template>
  <div class="backdrop" @click.self="emit('close')">
    <div class="modal card" role="dialog" :aria-label="title">
      <div class="head"><h2>{{ title }}</h2><button class="small" @click="emit('close')">关闭</button></div>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.backdrop { position: fixed; inset: 0; z-index: 40; background: rgba(0, 0, 0, 0.45); display: flex; align-items: center; justify-content: center; padding: 16px; }
.modal { width: min(900px, 100%); max-height: calc(100vh - 32px); overflow: auto; }
.head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.head h2 { margin: 0; }
</style>
