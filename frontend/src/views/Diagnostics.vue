<script setup lang="ts">
// 飞控诊断（原“飞行排障”）：说明它是什么、什么时候用，表单与诊断记录（1.2、1.12、1.15、1.31）
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { listFaults } from '../api'
import TaskHistory from '../components/tasks/TaskHistory.vue'
import Spinner from '../components/common/Spinner.vue'
import { useMissions } from '../stores/missions'
import { useSim } from '../stores/sim'

const router = useRouter()
const { sim, ready, phaseText } = useSim()
const { missions, live, troubleshoot } = useMissions()
const faults = ref<{ id: string; label: string; symptom: string }[]>([])
const fault = ref('')
const symptom = ref('')
const busy = ref(false)
const blocked = computed(() => {
  if (!ready.value) return `${phaseText.value}。诊断需要实际试飞，请先在场景库启动仿真。`
  if (live.value.length) return '飞机正在执行其他任务；诊断需要实际试飞，请等任务结束后再开始。'
  return ''
})
function pick() { const f = faults.value.find((x) => x.id === fault.value); if (f) symptom.value = f.symptom }
async function submit() {
  if (!symptom.value.trim() || busy.value || blocked.value) return
  busy.value = true
  const id = await troubleshoot(symptom.value.trim(), fault.value || null)
  busy.value = false
  if (id) router.push(`/tasks/${id}`)
}
onMounted(async () => { faults.value = await listFaults().catch(() => []) })
</script>

<template>
  <div class="page">
    <section class="card intro">
      <h2>飞控诊断是做什么的</h2>
      <p>飞机<b>飞起来有异常</b>时用，例如悬停抖动、转向特别慢、近照总对不准。诊断员会<b>实际起飞一次</b>按固定动作试飞，记录飞行日志指标，把飞控参数和“已知良好配置”对比，再一次只改一组参数做对照实验，直到找到原因。全程约 3–6 分钟，结束后参数恢复原样。</p>
      <p class="small muted">和“飞行前检查”不同：飞行前检查只看当前状态、不起飞、不花钱，每 15 秒自动进行；飞控诊断要试飞、会调用模型，用来查“为什么飞得不对”。</p>
    </section>
    <div class="grid">
      <section class="card">
        <h2>描述遇到的问题</h2>
        <textarea v-model="symptom" rows="4" placeholder="例如：悬停时机身有些抖动，拍出来的照片偶尔糊" />
        <label class="small muted" for="fault">演示：先在仿真中注入一个已知故障（诊断员不知道是哪一个），看它能否找出来</label>
        <select id="fault" v-model="fault" @change="pick">
          <option value="">不注入，诊断当前飞机</option>
          <option v-for="f in faults" :key="f.id" :value="f.id">{{ f.label }}</option>
        </select>
        <button class="primary wide" :disabled="!symptom.trim() || busy || !!blocked" @click="submit"><Spinner v-if="busy" :size="12" /> 开始诊断</button>
        <p v-if="blocked" class="small muted">{{ blocked }}<router-link v-if="!sim.scene" to="/scenes"> 去场景库</router-link></p>
        <ol class="how small muted">
          <li>基线试飞：起飞 8 米、悬停、横移 6 米、转向 90°、爬升 4 米、返回降落，约 1 分钟</li>
          <li>对比参数：与 PX4 默认的良好配置比较，列出差异（可能混有无害的自定义修改）</li>
          <li>对照实验：把怀疑的参数恢复后重飞，看异常指标是否恢复；一次只改一组</li>
          <li>结论：根因、前后对比的证据、对真机的修改建议</li>
        </ol>
      </section>
      <section class="card records">
        <h2>诊断记录</h2>
        <TaskHistory :missions="missions" :selected="null" kind="troubleshoot" @open="(id) => router.push(`/tasks/${id}`)" />
      </section>
    </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; gap: 14px; }
.intro p { margin: 0 0 6px; line-height: 1.7; font-size: 14px; }
.grid { display: grid; grid-template-columns: minmax(320px, 460px) minmax(0, 1fr); gap: 14px; align-items: start; }
textarea, select { width: 100%; margin: 6px 0 10px; }
.wide { width: 100%; }
.how { margin: 12px 0 0; padding-left: 18px; line-height: 1.7; }
.records { min-height: 300px; display: flex; flex-direction: column; }
@media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
</style>
