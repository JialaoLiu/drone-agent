<script setup lang="ts">
import { computed } from 'vue'
import { useSim } from '../../stores/sim'
import HealthChip from './HealthChip.vue'
import SimControl from './SimControl.vue'

defineProps<{ title: string }>()
const emit = defineEmits<{ menu: [] }>()
const { sim, returnHome } = useSim()
const t = computed(() => sim.value.telemetry)
// 一键返航只在飞机可能在空中时显示（已解锁、在空中或有任务在执行）；在地面时没有意义（1.1）
const canReturn = computed(() => !!(t.value.in_air || t.value.armed || sim.value.active_mission))
function confirmReturn() { if (window.confirm('立即返航？正在执行的任务会被取消。')) returnHome() }
</script>

<template>
  <header>
    <button class="menu small" @click="emit('menu')">菜单</button>
    <h1>{{ title }}</h1>
    <SimControl />
    <div v-if="sim.px4" class="chips">
      <span class="chip" :class="(t.battery ?? 100) < 30 ? 'bad' : ''">电量 {{ t.battery != null ? t.battery.toFixed(0) + '%' : '—' }}</span>
      <span class="chip">高度 {{ t.z != null ? t.z.toFixed(1) + ' 米' : '—' }}</span>
      <span class="chip">{{ t.in_air ? '飞行中' : t.armed ? '已解锁' : '地面' }}</span>
    </div>
    <HealthChip />
    <button v-if="canReturn" class="danger return" @click="confirmReturn">一键返航</button>
  </header>
</template>

<style scoped>
header { display: flex; align-items: center; gap: 10px; padding: 10px 18px; border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; z-index: 10; flex-wrap: wrap; }
h1 { font-size: 17px; margin: 0 6px 0 0; }
.chips { display: flex; gap: 6px; }
.return { margin-left: auto; }
.menu { display: none; }
@media (max-width: 860px) {
  .menu { display: inline-block; }
  .chips { display: none; }
  header { padding: 8px 12px; }
}
</style>
