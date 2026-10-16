<script setup lang="ts">
// 工作台：左侧下达任务与历史任务，右侧是欢迎页或当前任务（1.19、1.32）
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import TaskComposer from '../components/tasks/TaskComposer.vue'
import TaskHistory from '../components/tasks/TaskHistory.vue'
import { useMissions } from '../stores/missions'

const route = useRoute()
const router = useRouter()
const { missions } = useMissions()
const selected = computed(() => (route.params.id ? String(route.params.id) : null))
const open = (id: string) => router.push(`/tasks/${id}`)
</script>

<template>
  <div class="bench">
    <aside :class="{ hidden: !!selected }">
      <TaskComposer @created="open" />
      <TaskHistory :missions="missions" :selected="selected" @open="open" />
    </aside>
    <section class="detail"><router-view /></section>
  </div>
</template>

<style scoped>
.bench { display: grid; grid-template-columns: 340px minmax(0, 1fr); height: calc(100vh - 53px); }
aside { border-right: 1px solid var(--line); background: var(--panel); display: flex; flex-direction: column; min-height: 0; }
.detail { overflow-y: auto; padding: 16px 20px 40px; min-width: 0; }
@media (max-width: 960px) {
  .bench { grid-template-columns: 1fr; height: auto; }
  aside { border-right: 0; }
  aside.hidden { display: none; }              /* 手机上打开任务时只显示任务 */
  .detail { padding: 12px; }
}
</style>
