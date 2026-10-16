import type { Place, SceneInfo, Site, Truth } from '../types'
import { enc, post, request } from './client'

export const listScenes = () => request<SceneInfo[]>('/scenes')
export const createScene = (seed: number | null, location: Place | null = null) =>
  post<{ name: string; ground?: Site['ground'] }>('/scenes', { seed, location })
export const suggestPlaces = (q: string) => request<Place[]>(`/places/suggest?${new URLSearchParams({ q })}`)
export const getSite = (name: string) => request<Site>(`/site/${enc(name)}`)
export const getTruth = (name: string) => request<Truth>(`/site/${enc(name)}/truth`)
export const getBasemap = (scene: string) => request<{ available: boolean; reason?: string } & Record<string, any>>(`/site/${enc(scene)}/basemap`)
