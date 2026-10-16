<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { emergencyReturn } from './api'
import PreflightList from './components/PreflightList.vue'
import { useSim } from './composables/sim'

const { sim, ready, reachable, health } = useSim()
const showHealth = ref(false)
const healthLabel = computed(() => {
  const h = health.value
  if (!h) return null
  // 失败优先显示：检查超时时没有逐项结果，但失败结论和处理建议必须看得到（深度审查 M03）
  if (h.ok === false) return { text: h.items.length ? '飞行前检查不通过' : '飞行前检查无法完成', cls: 'bad' }
  if (!h.items.length) return null
  return h.items.some((i) => i.ok === null) ? { text: '飞行前检查：有提示', cls: 'warn' } : { text: '飞行前检查通过', cls: 'ok' }
})
const route = useRoute()
watch(() => route.fullPath, () => { showHealth.value = false })   // 切换页面时收起弹出框，避免挡住页面内容
const menu = ref(false)
const notice = ref('')

const NAV = [
  { to: '/', label: '总览', icon: 'M3 12l9-8 9 8v8a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z' },
  { to: '/missions', label: '巡检任务', icon: 'M12 2l3 7h7l-5.5 4.5L18.5 21 12 16.5 5.5 21l2-7.5L2 9h7z' },
  { to: '/troubleshoot', label: '飞行排障', icon: 'M14.7 6.3a4 4 0 0 0-5.4 5.4L3 18l3 3 6.3-6.3a4 4 0 0 0 5.4-5.4l-2.6 2.6-2.4-.6-.6-2.4z' },
  { to: '/scenes', label: '场景库', icon: 'M3 5h18v14H3zM3 15l5-5 4 4 3-3 6 6' },
  { to: '/usage', label: '用量与成本', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { to: '/evals', label: '评测结果', icon: 'M9 11l3 3 8-8M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9' },
  { to: '/help', label: '使用说明', icon: 'M12 18h.01M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z' },
]
const t = computed(() => sim.value.telemetry)
const active = (to: string) => (to === '/' ? route.path === '/' : route.path.startsWith(to))

async function returnHome() {
  if (!window.confirm('立即返航？正在执行的任务会被取消。')) return
  try { notice.value = (await emergencyReturn()).message } catch (e) { notice.value = (e as Error).message }
  setTimeout(() => (notice.value = ''), 5000)
}
</script>

<template>
  <div class="shell">
    <nav :class="{ open: menu }">
      <div class="brand">
        <svg viewBox="0 0 32 32" width="26" height="26"><polygon points="16,3 27,27 16,21 5,27" fill="currentColor" /></svg>
        <div><b>无人机巡检</b><small>多 Agent 协同平台</small></div>
      </div>
      <router-link v-for="n in NAV" :key="n.to" :to="n.to" class="nav-item" :class="{ on: active(n.to) }" @click="menu = false">
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="n.icon" /></svg>
        <span>{{ n.label }}</span>
      </router-link>
      <div class="nav-foot">
        <p><span class="dot" :class="ready ? 'ok' : sim.scene ? 'warn' : 'off'" />{{ !reachable ? '后端未连接' : ready ? `仿真运行中 · ${sim.scene}` : sim.scene ? `仿真启动中 · ${sim.scene}` : '仿真未启动' }}</p>
        <p v-if="sim.active_mission" class="small">执行中任务：<router-link :to="`/missions/${sim.active_mission}`">{{ sim.active_mission }}</router-link></p>
      </div>
    </nav>

    <div class="main">
      <header>
        <button class="menu" @click="menu = !menu">菜单</button>
        <h1>{{ (route.meta.title as string) ?? '' }}</h1>
        <div class="chips">
          <span class="chip" :class="(t.battery ?? 100) < 30 ? 'bad' : ''">电量 {{ t.battery != null ? t.battery.toFixed(0) + '%' : '—' }}</span>
          <span class="chip">高度 {{ t.z != null ? t.z.toFixed(1) + ' 米' : '—' }}</span>
          <span class="chip">{{ t.in_air ? '飞行中' : t.armed ? '已解锁' : '地面' }}</span>
          <span class="chip muted">{{ t.flight_mode ?? '—' }}</span>
          <span v-if="healthLabel" class="health">
            <button class="chip" :class="healthLabel.cls" @click="showHealth = !showHealth">{{ healthLabel.text }}</button>
            <div v-if="showHealth && health" class="health-pop card">
              <PreflightList v-if="health.items.length" :items="health.items" />
              <p v-if="health.advice" class="small bad-text">{{ health.advice }}</p>
              <p class="small muted">与安全员起飞前执行的是同一套检查，每 15 秒刷新。</p>
            </div>
          </span>
        </div>
        <!-- 不按 in_air 禁用：遥测可能延迟，紧急操作必须随时可按；飞机在地面时后端会说明 -->
        <button class="danger" @click="returnHome">一键返航</button>
      </header>
      <p v-if="notice" class="notice">{{ notice }}</p>
      <main>
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell { display: flex; min-height: 100vh; }
nav { width: 232px; flex-shrink: 0; background: var(--nav); color: var(--nav-text); display: flex; flex-direction: column; padding: 16px 12px;
  position: sticky; top: 0; height: 100vh; }
.brand { display: flex; align-items: center; gap: 10px; padding: 4px 8px 18px; color: #fff; }
.brand b { display: block; font-size: 16px; }
.brand small { color: var(--nav-muted); font-size: 11px; }
.nav-item { display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: 8px; color: var(--nav-text); text-decoration: none; font-size: 14px; }
.nav-item:hover { background: rgba(255, 255, 255, 0.06); }
.nav-item.on { background: rgba(255, 255, 255, 0.12); color: #fff; }
.nav-foot { margin-top: auto; font-size: 12px; color: var(--nav-muted); padding: 8px; border-top: 1px solid rgba(255, 255, 255, 0.08); }
.nav-foot p { margin: 6px 0 0; }
.nav-foot a { color: #9cc2ff; }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; background: #6b7280; }
.dot.ok { background: #4ade80; }
.dot.warn { background: #fbbf24; }
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
header { display: flex; align-items: center; gap: 10px; padding: 12px 22px; border-bottom: 1px solid var(--line); background: var(--panel); position: sticky; top: 0; z-index: 5; }
header h1 { font-size: 18px; margin: 0 12px 0 0; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { font-size: 12px; padding: 3px 10px; border-radius: 999px; background: var(--code); font-variant-numeric: tabular-nums; }
.chip.bad { color: var(--bad); }
button.chip { border: 0; cursor: pointer; font: inherit; font-size: 12px; }
.chip.ok { background: var(--ok-bg); color: var(--ok); }
.chip.warn { background: var(--warn-bg); color: var(--warn); }
.chip.bad { background: var(--bad-bg); }
.health { position: relative; }
.health-pop { position: absolute; top: 30px; right: 0; z-index: 20; width: min(460px, 86vw); padding: 12px; }
.bad-text { color: var(--bad); }
header .danger { margin-left: auto; }
.menu { display: none; }
.notice { margin: 10px 22px 0; padding: 8px 12px; border-radius: 8px; background: var(--warn-bg); color: var(--warn); font-size: 14px; }
main { padding: 18px 22px 40px; }
@media (max-width: 860px) {
  nav { position: fixed; inset: 0 auto 0 0; z-index: 20; transform: translateX(-100%); transition: transform 0.2s; }
  nav.open { transform: none; box-shadow: 0 0 24px rgba(0, 0, 0, 0.35); }
  .menu { display: block; }
  .chips > .chip:nth-child(n + 3) { display: none; }
  main { padding: 14px 16px 40px; }
}
</style>
