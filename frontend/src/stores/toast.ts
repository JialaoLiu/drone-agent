import { ref } from 'vue'

// 全局提示条：操作结果、错误、排队等反馈（右下角，几秒后消失）
export interface Toast { id: number; text: string; tone: 'ok' | 'warn' | 'bad' | 'info' }
const toasts = ref<Toast[]>([])
let next = 1

export function useToast() {
  function show(text: string, tone: Toast['tone'] = 'info', ms = 4500) {
    const id = next++
    toasts.value.push({ id, text, tone })
    window.setTimeout(() => { toasts.value = toasts.value.filter((t) => t.id !== id) }, ms)
  }
  return { toasts, show }
}
