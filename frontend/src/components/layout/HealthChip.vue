<script setup lang="ts">
// 飞行前检查标签：结论 + 弹出明细（检查时间、手动刷新）。只读飞控与本机状态和天气接口，不调用模型、不花钱（1.3）
import { computed, onBeforeUnmount, ref } from 'vue'
import { useSim } from '../../stores/sim'
import PreflightList from '../task/PreflightList.vue'
import Spinner from '../common/Spinner.vue'

const { health, healthLoading, refreshHealth, sim } = useSim()
const open = ref(false)
const now = ref(Date.now())
const timer = window.setInterval(() => (now.value = Date.now()), 1000)
onBeforeUnmount(() => window.clearInterval(timer))
const label = computed(() => {
  const h = health.value
  if (!h) return null
  if (h.ok === false) return { text: h.items.length ? '飞行前检查不通过' : '飞行前检查无法完成', cls: 'bad' }
  if (!h.items.length) return null
  return h.items.some((i) => i.ok === null) ? { text: '飞行前检查：有提示', cls: 'warn' } : { text: '飞行前检查通过', cls: 'ok' }
})
const ago = computed(() => (health.value?.checked_at ? Math.max(0, Math.round(now.value / 1000 - health.value.checked_at)) : null))
</script>

<template>
  <span v-if="sim.px4" class="health">
    <button class="chip" :class="label?.cls" @click="open = !open">
      <Spinner v-if="healthLoading && !label" :size="11" /> {{ label?.text ?? '飞行前检查中' }}
    </button>
    <div v-if="open" class="pop card">
      <div class="row">
        <b>飞行前检查</b>
        <span class="muted small">{{ ago != null ? `${ago} 秒前` : '' }}</span>
        <button class="small" :disabled="healthLoading" @click="refreshHealth"><Spinner v-if="healthLoading" :size="11" /> 立即检查</button>
      </div>
      <PreflightList v-if="health?.items.length" :items="health.items" />
      <p v-if="health?.advice" class="small bad-text">{{ health.advice }}</p>
      <p class="small muted">与安全员起飞前执行的是同一套检查：飞控自检、传感器、仿真速度、位置估计、电脑负载、天气。每 15 秒自动刷新；只读取飞控和本机状态、天气接口，不调用模型、不产生费用。</p>
    </div>
  </span>
</template>

<style scoped>
.health { position: relative; }
.pop { position: absolute; top: 32px; right: 0; z-index: 25; width: min(480px, 88vw); padding: 12px; }
.row { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.row button { margin-left: auto; }
.bad-text { color: var(--bad); }
</style>
