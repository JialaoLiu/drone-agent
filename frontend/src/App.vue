<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute } from 'vue-router'
import Toasts from './components/common/Toasts.vue'
import NavRail from './components/layout/NavRail.vue'
import TopBar from './components/layout/TopBar.vue'

const route = useRoute()
const menu = ref(false)
const title = computed(() => (route.meta.title as string) ?? '')
</script>

<template>
  <div class="shell">
    <NavRail :open="menu" @close="menu = false" />
    <div class="main">
      <TopBar :title="title" @menu="menu = !menu" />
      <main :class="{ flush: route.meta.flush }"><router-view /></main>
    </div>
    <Toasts />
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
main { padding: 16px 20px 40px; }
main.flush { padding: 0; flex: 1; min-height: 0; }
@media (max-width: 860px) { main { padding: 12px 12px 40px; } }
</style>
