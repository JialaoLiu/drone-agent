<script setup lang="ts">
// 顶栏的仿真控制：任何页面都能看到状态并停止 / 重启 / 更换场景（1.39）；启动中显示阶段与进度条（1.29）
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useSim } from '../../stores/sim'
import Spinner from '../common/Spinner.vue'
import ThinkingText from '../common/ThinkingText.vue'

const { sim, phase, phaseText, start, stop } = useSim()
const router = useRouter()
const open = ref(false)
const dot = computed(() => ({ ready: 'ok', busy: 'ok', starting: 'warn', checking: 'warn', blocked: 'bad', error: 'bad' } as Record<string, string>)[phase.value] ?? 'off')
const scene = computed(() => sim.value.scene ?? sim.value.job?.scene)
async function act(kind: 'stop' | 'restart' | 'change') {
  open.value = false
  if (kind === 'stop') await stop()
  else if (kind === 'restart' && scene.value) await start(scene.value)
  else router.push('/scenes')
}
</script>

<template>
  <div class="sim">
    <button class="chip status" @click="open = !open">
      <span class="dot" :class="dot" />
      <Spinner v-if="phase === 'starting'" :size="11" />
      <ThinkingText v-if="phase === 'starting'" :text="phaseText" />
      <span v-else>{{ phaseText }}{{ scene && phase !== 'stopped' ? ` · ${scene}` : '' }}</span>
    </button>
    <div v-if="phase === 'starting'" class="bar"><i /></div>
    <div v-if="open" class="menu card">
      <p v-if="sim.job?.state === 'error'" class="small bad-text">上次启动失败：{{ sim.job.error }}</p>
      <button class="small" :disabled="!scene || !!sim.active_mission" @click="act('restart')">{{ phase === 'stopped' || phase === 'error' ? '启动' : '重启' }} {{ scene ?? '' }}</button>
      <button class="small" :disabled="phase === 'stopped' || !!sim.active_mission" @click="act('stop')">停止仿真</button>
      <button class="small" @click="act('change')">更换场景</button>
      <p v-if="sim.active_mission" class="small muted">有任务在执行，结束后才能停止或更换。</p>
    </div>
  </div>
</template>

<style scoped>
.sim { position: relative; }
.status { display: inline-flex; align-items: center; gap: 6px; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #9ca3af; }
.dot.ok { background: #22c55e; } .dot.warn { background: #f59e0b; } .dot.bad { background: #ef4444; }
.bar { position: absolute; left: 0; right: 0; bottom: -5px; height: 2px; overflow: hidden; border-radius: 2px; background: var(--line); }
.bar i { position: absolute; width: 35%; height: 100%; background: var(--accent); animation: slide 1.4s ease-in-out infinite; }
@keyframes slide { from { left: -35%; } to { left: 100%; } }
.menu { position: absolute; top: 32px; left: 0; z-index: 25; display: flex; flex-direction: column; gap: 6px; min-width: 220px; padding: 10px; }
.bad-text { color: var(--bad); margin: 0; }
</style>
