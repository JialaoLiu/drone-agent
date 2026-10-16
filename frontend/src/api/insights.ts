// 统计、用量、评测、设置
import type { Settings } from '../types'
import { enc, put, request } from './client'

export const getStats = () => request<Record<string, any>>('/stats')
export const getUsageDetail = (days: number) => request<Record<string, any>>(`/usage/detail?days=${days}`)
export const listEvals = () => request<{ name: string; suite: string; summary: any; scenes?: string[]; repeat?: number; model?: string; summary_rows?: any[] | null }[]>('/evals')
export const getEval = (name: string) => request<Record<string, any>>(`/evals/${enc(name)}`)
export const getSettings = () => request<Settings>('/settings')
export const saveSettings = (values: Partial<Settings>) => put<Settings>('/settings', values)
