<script setup lang="ts">
// 下达任务：当前场景、示例（按当前场景生成）、自动起飞；执行中再下达会排队
import { computed, ref, watch } from 'vue'
import { scenarioOf } from '../../scenarios'
import { useMissions } from '../../stores/missions'
import { useSim } from '../../stores/sim'
import { useSites } from '../../stores/sites'
import type { Site } from '../../types'
import Spinner from '../common/Spinner.vue'

const emit = defineEmits<{ created: [id: string] }>()
const { sim, ready, phaseText } = useSim()
const { create, live } = useMissions()
const { load } = useSites()
const site = ref<Site | null>(null)
watch(() => sim.value.scene, async (name) => { site.value = await load(name) }, { immediate: true })
const scenario = computed(() => scenarioOf(site.value?.scenario))
const examples = computed(() => scenario.value.examples(site.value))
const task = ref('')
const auto = ref(false)
const busy = ref(false)
const showExamples = ref(false)
const blocked = computed(() => (!ready.value ? phaseText.value + '，' + (sim.value.scene ? '请稍候' : '请先在场景库启动一个场景') : ''))

async function submit() {
  if (!task.value.trim() || busy.value || blocked.value) return
  busy.value = true
  const id = await create(task.value.trim(), auto.value)
  busy.value = false
  if (id) { task.value = ''; emit('created', id) }
}
</script>

<template>
  <section class="composer">
    <div class="scene">
      <span class="muted small">场景</span>
      <b>{{ sim.scene ?? '未启动' }}</b>
      <span v-if="site" class="muted small">{{ scenario.label }} · {{ site.arrays.length }} {{ scenario.group }} · {{ site.panels.length }} {{ scenario.item }}{{ site.location ? ' · ' + site.location.name : '' }}</span>
      <router-link to="/scenes" class="small">更换</router-link>
    </div>
    <textarea v-model="task" rows="3" :placeholder="`用一句话描述${scenario.verb}任务，例如：${examples[0]?.text ?? ''}`" @keydown.enter.meta.prevent="submit" @keydown.enter.ctrl.prevent="submit" />
    <div class="row">
      <button class="link small" @click="showExamples = !showExamples">{{ showExamples ? '收起示例' : '示例' }}</button>
      <label class="small"><input v-model="auto" type="checkbox" /> 通过安全校验后自动起飞</label>
    </div>
    <div v-if="showExamples" class="examples">
      <button v-for="e in examples" :key="e.text" class="example" @click="task = e.text; showExamples = false"><span>{{ e.text }}</span><small>{{ e.note }}</small></button>
    </div>
    <button class="primary wide" :disabled="!task.trim() || busy || !!blocked" @click="submit">
      <Spinner v-if="busy" :size="12" /> {{ live.length ? '加入队列' : '下达任务' }}
    </button>
    <p v-if="blocked" class="small muted reason">{{ blocked }}</p>
    <p v-else-if="live.length" class="small muted reason">有 {{ live.length }} 个任务在进行，新任务会排队，按顺序执行。</p>
  </section>
</template>

<style scoped>
.composer { display: flex; flex-direction: column; gap: 8px; padding: 14px; border-bottom: 1px solid var(--line); }
.scene { display: flex; flex-wrap: wrap; gap: 4px 8px; align-items: baseline; }
textarea { width: 100%; resize: vertical; }
.row { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.link { border: 0; background: none; color: var(--accent); padding: 0; }
.examples { display: flex; flex-direction: column; gap: 4px; }
.example { text-align: left; border-radius: 8px; padding: 5px 9px; display: flex; flex-direction: column; gap: 1px; }
.example span { font-size: 13px; }
.example small { font-size: 11px; color: var(--muted); }
.wide { width: 100%; }
.reason { margin: 0; }
</style>
