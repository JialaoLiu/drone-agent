<script setup lang="ts">
// 调度员追问时，在同一个任务里回复（不新开任务、不丢上下文）
import { computed, ref } from 'vue'
import { replyMission } from '../../api'
import { useToast } from '../../stores/toast'
import type { MissionEvent } from '../../types'
import Spinner from '../common/Spinner.vue'

const props = defineProps<{ missionId: string; events: MissionEvent[] }>()
const emit = defineEmits<{ sent: [] }>()
const { show } = useToast()
const text = ref('')
const auto = ref(false)
const busy = ref(false)
const question = computed(() => [...props.events].reverse().find((e) => e.kind === 'clarify')?.message ?? '调度员需要补充信息')
async function send() {
  if (!text.value.trim() || busy.value) return
  busy.value = true
  try {
    const r = await replyMission(props.missionId, text.value.trim(), auto.value)
    show(r.queued ? `已补充，排队中（前面还有 ${r.ahead} 个任务）` : '已补充，调度员重新理解任务', 'ok')
    text.value = ''
    emit('sent')
  } catch (e) { show((e as Error).message, 'bad') } finally { busy.value = false }
}
</script>

<template>
  <section class="reply card">
    <div class="q"><span class="role">调度员</span><p>{{ question }}</p></div>
    <textarea v-model="text" rows="2" placeholder="在这里回复，例如：三列都要查；不知道特征也可以直接说“不知道”" @keydown.enter.meta.prevent="send" @keydown.enter.ctrl.prevent="send" />
    <div class="row">
      <label class="small"><input v-model="auto" type="checkbox" /> 通过安全校验后自动起飞</label>
      <button class="primary" :disabled="!text.trim() || busy" @click="send"><Spinner v-if="busy" :size="12" /> 回复</button>
    </div>
  </section>
</template>

<style scoped>
.reply { margin-bottom: 14px; border-color: var(--accent); }
.q { display: flex; gap: 10px; align-items: baseline; }
.q p { margin: 0; line-height: 1.6; }
.role { font-size: 12px; padding: 1px 8px; border-radius: 999px; background: #e0e7ff; color: #3730a3; white-space: nowrap; }
textarea { width: 100%; margin: 10px 0 8px; resize: vertical; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
</style>
