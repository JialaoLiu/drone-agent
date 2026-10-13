export interface Telemetry {
  x: number; y: number; z: number; yaw: number; battery: number; armed: boolean; in_air: boolean
  flight_mode: string; gimbal_pitch: number; status: string[]; updated: number
}
export interface SimStatus { scene: string | null; camera: boolean; px4: boolean; telemetry: Partial<Telemetry>; active_mission: string | null }
export interface SceneInfo { name: string; seed: number; panels: number; arrays: number }
export interface Panel { id: string; array: string; row: number; col: number; center: number[]; corners: number[][] }
export interface Site {
  name: string; seed: number; geofence: number[][]; max_altitude: number; min_altitude: number
  no_fly_zones: { id: string; label: string; center: number[]; radius: number }[]
  obstacles: { id: string; label: string; center: number[]; radius: number; height: number }[]
  arrays: { id: string; center: number[]; rows: number; cols: number; size: number[] }[]
  panels: Panel[]
}
export interface Truth { defects: { panel_id: string; type: string; label: string; severity: string }[] }
export interface MissionEvent { seq: number; role: string; kind: string; message: string; payload: Record<string, unknown>; created_at: string }
export interface Finding {
  panel_id: string; status: 'suspect' | 'confirmed' | 'cleared' | 'unverified'; type: string | null
  confidence: number | null; evidence: string | null; screen_photo: number | null; verify_photo: number | null
}
export interface Photo { id: number; step: number; kind: string; target: string; camera: string; meta: Record<string, unknown> }
export interface RouteStep { kind: string; target: string; position: number[]; yaw: number; pitch: number }
export interface Plan {
  targets: string[]; closeup_policy: string; standoff_m: number; sorties: string[][]; estimated_minutes: number
  estimated_battery: number[]; rationale: string; violations: { code: string; message: string; suggestion: string }[]; route?: RouteStep[]
}
export interface MissionSummary { id: string; task: string; scene: string; status: string; created_at: string; updated_at: string }
export interface Mission extends MissionSummary {
  brief: Record<string, unknown> | null; plan: Plan | null; result: Record<string, unknown> | null
  findings: Finding[]; photos: Photo[]; usage: { total: { calls: number; cost_yuan: number; images: number } }
}
