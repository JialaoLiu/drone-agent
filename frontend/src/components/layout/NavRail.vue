<script setup lang="ts">
import { useRoute } from 'vue-router'

defineProps<{ open: boolean }>()
const emit = defineEmits<{ close: [] }>()
const route = useRoute()
const NAV = [
  { to: '/', label: '工作台', icon: 'M3 4h7v16H3zM14 4h7v7h-7zM14 15h7v5h-7z' },
  { to: '/diagnostics', label: '飞控诊断', icon: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z' },
  { to: '/scenes', label: '场景库', icon: 'M3 5h18v14H3zM3 15l5-5 4 4 3-3 6 6' },
  { to: '/usage', label: '用量与成本', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { to: '/evals', label: '评测结果', icon: 'M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9' },
  { to: '/settings', label: '设置', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z' },
  { to: '/help', label: '使用说明', icon: 'M12 18h.01M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z' },
]
const active = (to: string) => (to === '/' ? route.path === '/' || route.path.startsWith('/tasks') : route.path.startsWith(to))
</script>

<template>
  <div v-if="open" class="backdrop" @click="emit('close')" />
  <nav :class="{ open }">
    <div class="brand">
      <svg viewBox="0 0 32 32" width="24" height="24"><polygon points="16,3 27,27 16,21 5,27" fill="currentColor" /></svg>
      <div><b>无人机协同平台</b><small>多 Agent · 仿真</small></div>
      <button class="close" aria-label="收起菜单" @click="emit('close')">×</button>
    </div>
    <router-link v-for="n in NAV" :key="n.to" :to="n.to" class="item" :class="{ on: active(n.to) }" @click="emit('close')">
      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="n.icon" /></svg>
      <span>{{ n.label }}</span>
    </router-link>
  </nav>
</template>

<style scoped>
nav { width: 188px; flex-shrink: 0; background: var(--nav); color: var(--nav-text); display: flex; flex-direction: column; gap: 2px; padding: 14px 10px;
  position: sticky; top: 0; height: 100vh; }
.brand { display: flex; align-items: center; gap: 9px; padding: 2px 6px 16px; color: #fff; }
.brand b { display: block; font-size: 14px; }
.brand small { color: var(--nav-muted); font-size: 11px; }
.close { display: none; margin-left: auto; background: transparent; border: 0; color: var(--nav-text); font-size: 22px; padding: 0 6px; }
.item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border-radius: 8px; color: var(--nav-text); text-decoration: none; font-size: 14px; transition: background 0.15s; }
.item:hover { background: rgba(255, 255, 255, 0.07); }
.item.on { background: rgba(255, 255, 255, 0.14); color: #fff; }
.backdrop { display: none; }
@media (max-width: 860px) {
  nav { position: fixed; inset: 0 auto 0 0; z-index: 30; transform: translateX(-100%); transition: transform 0.2s; width: 220px; }
  nav.open { transform: none; box-shadow: 0 0 24px rgba(0, 0, 0, 0.35); }
  .close { display: block; }
  .backdrop { display: block; position: fixed; inset: 0; z-index: 29; background: rgba(0, 0, 0, 0.35); }
}
</style>
