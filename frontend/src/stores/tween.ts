import { onBeforeUnmount, ref, watch, type Ref } from 'vue'

// 遥测约每秒一帧，直接跳到新位置看起来一卡一卡（1.21）。这里在两帧之间按时间插值，转向走最短角度。
export interface Pose { x: number; y: number; yaw: number }

export function useTweenedPose(source: Ref<Pose | null>, duration = 900) {
  const pose = ref<Pose | null>(source.value ? { ...source.value } : null)
  let from: Pose | null = null
  let to: Pose | null = null
  let t0 = 0
  let raf = 0
  let last = 0
  const step = (now: number) => {
    if (!from || !to) return
    // 约每秒 20 帧，并且页面不在前台时不画：地图动画不能和仿真抢电脑资源（DR-085）
    if (document.hidden || now - last < 50) { raf = requestAnimationFrame(step); if (document.hidden) { pose.value = { ...to }; cancelAnimationFrame(raf) } return }
    last = now
    const k = Math.min(1, (now - t0) / duration)
    const e = k < 0.5 ? 2 * k * k : 1 - (-2 * k + 2) ** 2 / 2     // 缓入缓出
    let dyaw = ((to.yaw - from.yaw + 540) % 360) - 180
    pose.value = { x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e, yaw: from.yaw + dyaw * e }
    if (k < 1) raf = requestAnimationFrame(step)
  }
  watch(source, (p) => {
    if (!p) { pose.value = null; return }
    if (!pose.value) { pose.value = { ...p }; return }
    cancelAnimationFrame(raf)
    from = { ...pose.value }
    to = { ...p }
    t0 = performance.now()
    raf = requestAnimationFrame(step)
  }, { deep: true })
  onBeforeUnmount(() => cancelAnimationFrame(raf))
  return pose
}
