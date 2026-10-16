import type { Mission, MissionSummary } from '../types'
import { enc, post, request, wsUrl } from './client'

export const listMissions = () => request<MissionSummary[]>('/missions')
export const getMission = (id: string) => request<Mission>(`/missions/${enc(id)}`)
export const createMission = (task: string, autoApprove: boolean) =>
  post<{ id: string; queued: boolean; ahead?: number }>('/missions', { task, auto_approve: autoApprove })
export const approveMission = (id: string, approved: boolean) => post<{ status: string }>(`/missions/${enc(id)}/approve`, { approved })
export const cancelMission = (id: string) => post<{ status: string }>(`/missions/${enc(id)}/cancel`)
export const getScore = (id: string) => request<Record<string, any>>(`/missions/${enc(id)}/score`)
export const listFaults = () => request<{ id: string; label: string; symptom: string }[]>('/troubleshoot/faults')
export const startTroubleshoot = (symptom: string, fault: string | null) => post<{ id: string }>('/troubleshoot', { symptom, fault })
export const reportUrl = (id: string) => `/api/missions/${enc(id)}/report`
export const eventsUrl = (id: string, after: number) => wsUrl(`/missions/${enc(id)}/events`, { after: String(after) })
export const photoUrl = (id: number, overlay = false, width = 960) => `/api/photos/${id}?${new URLSearchParams({ overlay: String(overlay), width: String(width) })}`
export const panelUrl = (photoId: number, panelId: string) => `/api/photos/${photoId}/panel/${enc(panelId)}`
