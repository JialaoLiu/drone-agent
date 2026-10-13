<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listFaults, startTroubleshoot } from '../api'
import { useSim } from '../composables/sim'

const router = useRouter()
const { sim, ready } = useSim()
const faults = ref<{ id: string; label: string; symptom: string }[]>([])
const fault = ref('')
const symptom = ref('')
const busy = ref(false)
const error = ref('')

function pick() {
  const f = faults.value.find((x) => x.id === fault.value)
  if (f) symptom.value = f.symptom
}

async function submit() {
  if (!symptom.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const { id } = await startTroubleshoot(symptom.value.trim(), fault.value || null)
    router.push(`/missions/${id}`)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

onMounted(async () => { try { faults.value = await listFaults() } catch { faults.value = [] } })
</script>

<template>
  <div class="page">
    <section class="card">
      <h2>描述飞行中遇到的问题</h2>
      <textarea v-model="symptom" rows="4" placeholder="例如：转机头特别慢，近距复核时相机总是对不准" />
      <label class="small muted" for="fault">演示：先在仿真中注入一个已知故障（诊断员不知道是哪一个）</label>
      <select id="fault" v-model="fault" @change="pick">
        <option value="">不注入，诊断当前飞机</option>
        <option v-for="f in faults" :key="f.id" :value="f.id">{{ f.label }}</option>
      </select>
      <p v-if="error" class="error">{{ error }}</p>
      <button class="primary wide" :disabled="!symptom.trim() || busy || !ready || !!sim.active_mission" @click="submit">开始排障</button>
      <p v-if="!ready" class="small muted">需要先在 <router-link to="/scenes">场景库</router-link> 启动仿真。</p>
    </section>
    <section class="card how">
      <h2>诊断员怎么工作</h2>
      <ol>
        <li><b>基线试飞</b>：按固定动作飞一次（起飞 8 米、悬停、横移 6 米、转向 90°、爬升 4 米、返回降落），约 1 分钟，记录日志指标。</li>
        <li><b>对比参数</b>：读取飞控参数，与“已知良好配置”（PX4 固件默认值）对比，列出差异。差异里可能混有无害的自定义修改。</li>
        <li><b>提出假设、做实验</b>：把怀疑的参数恢复为参考值后重飞，看异常指标是否恢复正常；一次只改一组，变量单一。</li>
        <li><b>给出结论</b>：根因、指标前后对比的证据、对真机的修改建议。所有实验只在仿真中进行，结束后参数恢复为良好配置。</li>
      </ol>
      <p class="small muted">评测：4 个注入故障的场景，两次独立运行均判断正确；“哪个参数改得最多就是原因”的简单规则 0/4。</p>
    </section>
  </div>
</template>

<style scoped>
.page { display: grid; grid-template-columns: minmax(320px, 460px) minmax(0, 1fr); gap: 14px; align-items: start; }
textarea, select { width: 100%; margin: 6px 0 10px; }
.wide { width: 100%; }
.error { color: var(--bad); font-size: 13px; }
.how ol { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 14px; }
@media (max-width: 900px) { .page { grid-template-columns: 1fr; } }
</style>
