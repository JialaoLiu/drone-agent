export interface Telemetry {
  x: number; y: number; z: number; yaw: number; battery: number; armed: boolean; in_air: boolean
  flight_mode: string; gimbal_pitch: number; status: string[]; updated: number
}
export interface SimJob { state: 'idle' | 'starting' | 'error'; scene: string | null; stage: string; error: string; started_at: number }
export interface SimStatus {
  scene: string | null; camera: boolean; px4: boolean; telemetry: Partial<Telemetry>; active_mission: string | null; job?: SimJob
}
export interface SceneInfo {
  name: string; seed: number; panels: number; arrays: number; scenario?: string; location?: string | null; ground?: string; created_at?: number
}
export interface Place { name: string; address: string; city: string; lat: number; lon: number; source: string }
export interface Settings {
  vision_thinking: 'off' | 'screen' | 'on'; segment_screen: boolean; recheck_thinking: boolean
  fields?: Record<string, { label: string; help?: string; options?: Record<string, string> }>
}
export interface Panel { id: string; array: string; row: number; col: number; center: number[]; corners: number[][] }
export interface Site {
  name: string; seed: number; geofence: number[][]; max_altitude: number; min_altitude: number
  scenario?: string; origin?: { lat: number; lon: number; alt?: number }
  landmarks?: { id: string; label: string; center: number[] }[]
  no_fly_zones: { id: string; label: string; center: number[]; radius: number }[]
  obstacles: { id: string; label: string; center: number[]; radius: number; height: number }[]
  arrays: { id: string; center: number[]; rows: number; cols: number; size: number[] }[]
  panels: Panel[]
  location?: Place | null
  ground?: { source: 'procedural'; extent_m: number; note: string } | null
  features?: { kind: 'water' | 'building' | 'wood' | 'solar' | 'aeroway'; polygon: number[][]; height?: number }[] | null
  twin?: Record<string, any> | null
  terrain?: { relief: number; origin_elevation: number } | null
}
export interface TruthPerson { id: string; role: 'target' | 'bystander'; state: string; start: number[]; top: string; bottom: string; child: boolean }
export interface Truth {
  defects: { panel_id: string; type: string; label: string; severity: string }[]
  people?: TruthPerson[]; decoys?: { id: string; color: string; center: number[] }[]; brief?: { description: string; last_seen_label: string }
}
export interface Marker { x: number; y: number; label: string; tone: 'target' | 'bad' | 'warn' | 'muted' | 'info' }
export interface MissionEvent { seq: number; role: string; kind: string; message: string; payload: Record<string, unknown>; created_at: string }
export interface Finding {
  panel_id: string; status: 'suspect' | 'confirmed' | 'cleared' | 'unverified'; type: string | null
  confidence: number | null; evidence: string | null; screen_photo: number | null; verify_photo: number | null
  reason?: 'misaimed' | 'unclear' | 'no_budget' | 'model_down' | null
}
export interface Photo { id: number; step: number; kind: string; target: string; camera: string; meta: Record<string, unknown> }
export interface RouteStep { kind: string; target: string; position: number[]; yaw: number; pitch: number }
export interface PreflightItem { key: string; label: string; ok: boolean | null; detail: string }
export interface Preflight { ok: boolean | null; items: PreflightItem[]; advice: string; checked_at?: number }

export interface Plan {
  targets: string[]; closeup_policy: string; standoff_m: number; sorties: string[][]; estimated_minutes: number
  estimated_battery: number[]; rationale: string; violations: { code: string; message: string; suggestion: string }[]; route?: RouteStep[]
  preflight?: Preflight
  routes?: RouteStep[][]
}
export interface MissionSummary {
  id: string; task: string; scene: string; status: string; created_at: string; updated_at: string; kind?: 'mission' | 'troubleshoot'
}
export interface Mission extends MissionSummary {
  brief: Record<string, unknown> | null; plan: Plan | null; result: Record<string, unknown> | null
  findings: Finding[]; photos: Photo[]; usage: { total: { calls: number; cost_yuan: number; images: number } }
}
