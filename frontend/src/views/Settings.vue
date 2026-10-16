<script setup lang="ts">
// 设置：与准确率、成本取舍有关的开关，保存后立即生效（1.40）。安全规则不在这里，不能从网页关闭。
import { onMounted, ref } from 'vue'
import { getSettings, saveSettings } from '../api'
import Spinner from '../components/common/Spinner.vue'
import { useToast } from '../stores/toast'
import type { Settings } from '../types'

const { show } = useToast()
const s = ref<Settings | null>(null)
const saving = ref(false)
onMounted(async () => { s.value = await getSettings().catch(() => null) })
async function save(values: Partial<Settings>) {
  saving.value = true
  try { s.value = await saveSettings(values); show('已保存，下一次调用起生效', 'ok') } catch (e) { show((e as Error).message, 'bad') } finally { saving.value = false }
}
</script>

<template>
  <div v-if="s" class="settings">
    <section class="card">
      <h2>视觉模型推理过程</h2>
      <p class="small muted">推理过程按输出计费，约占视觉模型输出的 90%。对照实验：关闭后费用省 75%–86%，近照复核后的检出率、精确率不变。</p>
      <label v-for="(label, k) in s.fields?.vision_thinking?.options" :key="k" class="opt">
        <input type="radio" :checked="s.vision_thinking === k" :disabled="saving" @change="save({ vision_thinking: k as any })" /> {{ label }}
      </label>
    </section>
    <section class="card">
      <h2>拍摄与复核</h2>
      <label class="opt"><input type="checkbox" :checked="s.segment_screen" :disabled="saving" @change="save({ segment_screen: !s.segment_screen })" />
        {{ s.fields?.segment_screen?.label }}<span class="small muted">　{{ s.fields?.segment_screen?.help }}</span></label>
      <label class="opt"><input type="checkbox" :checked="s.recheck_thinking" :disabled="saving" @change="save({ recheck_thinking: !s.recheck_thinking })" />
        {{ s.fields?.recheck_thinking?.label }}<span class="small muted">　{{ s.fields?.recheck_thinking?.help }}</span></label>
    </section>
    <p class="small muted"><Spinner v-if="saving" :size="11" /> 设置保存在数据目录的 settings.json，服务重启后仍然有效；安全规则（围栏、禁飞区、电量、飞行前检查）不能在这里关闭。</p>
  </div>
  <p v-else class="muted small">加载中……</p>
</template>

<style scoped>
.settings { display: flex; flex-direction: column; gap: 14px; max-width: 760px; }
.opt { display: block; padding: 6px 0; font-size: 14px; }
</style>
