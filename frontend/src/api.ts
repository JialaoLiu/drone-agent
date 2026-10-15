import type { Mission, MissionSummary, Preflight, SceneInfo, SimStatus, Site, Truth } from './types'

// 后端设置了 DRONE_AGENT_ACCESS_TOKEN 时，所有请求都要带访问令牌；令牌只保存在本机浏览器
const TOKEN_KEY = 'drone_access_token'

export function accessToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) ?? '' } catch { return '' }
}

function askForToken(): boolean {
  const token = window.prompt('后端要求访问令牌，请输入：')?.trim()
  if (!token) return false
  try { localStorage.setItem(TOKEN_KEY, token) } catch { /* 隐私模式下不可写 */ }
  return true
}

async function json<T>(url: string, init: RequestInit = {}, retried = false): Promise<T> {
  const response = await fetch(`/api${url}`, { ...init, headers: { ...init.headers, 'X-Access-Token': accessToken(), 'Content-Type': 'application/json' } })
  if (response.status === 401 && !retried && askForToken()) return json<T>(url, init, true)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`)
  return body as T
}

export const getSim = () => json<SimStatus>('/sim')
export const getHealth = () => json<Preflight>('/sim/health')
export const startSim = (scene: string) => json<SimStatus>('/sim/start', { method: 'POST', body: JSON.stringify({ scene }) })
export const stopSim = () => json<SimStatus>('/sim/stop', { method: 'POST' })
export const emergencyReturn = () => json<{ message: string }>('/sim/return', { method: 'POST' })
export const listScenes = () => json<SceneInfo[]>('/scenes')
export const createScene = (seed: number) => json<{ name: string }>('/scenes', { method: 'POST', body: JSON.stringify({ seed }) })
export const getSite = (name: string) => json<Site>(`/site/${encodeURIComponent(name)}`)
export const getTruth = (name: string) => json<Truth>(`/site/${encodeURIComponent(name)}/truth`)
export const listMissions = () => json<MissionSummary[]>('/missions')
export const getMission = (id: string) => json<Mission>(`/missions/${encodeURIComponent(id)}`)
export const createMission = (task: string, autoApprove: boolean) =>
  json<{ id: string }>('/missions', { method: 'POST', body: JSON.stringify({ task, auto_approve: autoApprove }) })
export const approveMission = (id: string, approved: boolean) =>
  json<{ status: string }>(`/missions/${encodeURIComponent(id)}/approve`, { method: 'POST', body: JSON.stringify({ approved }) })
export const cancelMission = (id: string) => json<{ status: string }>(`/missions/${encodeURIComponent(id)}/cancel`, { method: 'POST' })
export const listFaults = () => json<{ id: string; label: string; symptom: string }[]>('/troubleshoot/faults')
export const startTroubleshoot = (symptom: string, fault: string | null) =>
  json<{ id: string }>('/troubleshoot', { method: 'POST', body: JSON.stringify({ symptom, fault }) })
export const getStats = () => json<Record<string, any>>('/stats')
export const getUsageDetail = (days: number) => json<Record<string, any>>(`/usage/detail?days=${days}`)
export const listEvals = () => json<{ name: string; suite: string; summary: any; scenes?: string[]; repeat?: number; model?: string }[]>('/evals')
export const getEval = (name: string) => json<Record<string, any>>(`/evals/${encodeURIComponent(name)}`)
export const getScore = (id: string) => json<Record<string, any>>(`/missions/${encodeURIComponent(id)}/score`)

export function panelUrl(photoId: number, panelId: string): string {
  return `/api/photos/${photoId}/panel/${encodeURIComponent(panelId)}`
}

export const getUsage = () => json<{ by_role: Record<string, number | string>[]; total: Record<string, number> }>('/usage')

export function photoUrl(id: number, overlay = false, width = 960): string {
  const params = new URLSearchParams({ overlay: String(overlay), width: String(width) })
  return `/api/photos/${id}?${params}`
}

export function reportUrl(id: string): string {
  return `/api/missions/${encodeURIComponent(id)}/report`
}

// 任务事件的 WebSocket 地址；令牌放在查询参数里（浏览器的 WebSocket 不能自定义请求头）
export function eventsUrl(id: string, after: number): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const params = new URLSearchParams({ after: String(after) })
  if (accessToken()) params.set('token', accessToken())
  return `${proto}://${location.host}/api/missions/${encodeURIComponent(id)}/events?${params}`
}

export const ROLE_LABELS: Record<string, string> = {
  coordinator: '调度员', planner: '规划员', safety: '安全员', pilot: '飞手', inspector: '巡检员',
  reporter: '报告员', diagnostician: '诊断员', system: '系统',
}
export const STATUS_LABELS: Record<string, string> = {
  planning: '规划中', awaiting_approval: '等待确认', running: '执行中', done: '已完成', failed: '失败',
  cancelled: '已取消', rejected: '未通过安全校验', clarify: '需要补充', unsupported: '无法执行',
}
export const DEFECT_LABELS: Record<string, string> = { crack: '裂纹', soiling: '污损', broken: '玻璃破碎', missing: '缺失' }
