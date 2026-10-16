import type { Preflight, SimStatus } from '../types'
import { post, request } from './client'

export const getSim = () => request<SimStatus>('/sim')
export const getHealth = () => request<Preflight>('/sim/health')
export const startSim = (scene: string) => post<SimStatus>('/sim/start', { scene })
export const stopSim = () => post<SimStatus>('/sim/stop')
export const emergencyReturn = () => post<{ message: string }>('/sim/return')
export const liveViewUrl = (camera: 'wide' | 'zoom', tick: number) => `/api/sim/view?camera=${camera}&t=${tick}`
