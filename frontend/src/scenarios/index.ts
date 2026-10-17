// 应用场景注册表：不同场景的术语、示例任务、图例、发现类型集中在这里，页面按注册表渲染，不写死某个场景。
// 新增场景时在这里加一项（并在后端提供对应的场景生成、规划与判断），界面其余部分不用改。
import type { Site } from '../types'

export interface Example { text: string; note: string }
export interface Stage { key: string; label: string; kinds: string[] }
export interface LegendItem { color: string; label: string; shape?: 'square' | 'dash' | 'circle' }
export interface Verdict { tone: 'ok' | 'warn' | 'bad'; title: string; advice: string; metrics: { label: string; value: string; help?: string }[] }
export interface Scenario {
  id: string
  label: string              // 场景名称
  verb: string               // 任务动作（巡检、搜索……）
  target: string             // 要找的东西（缺陷、人员……）
  group: string              // 场地里的分组单位（列、区域……）
  item: string               // 单个对象（板、目标……）
  findingTypes: Record<string, string>
  legend: LegendItem[]
  examples: (site: Site | null) => Example[]
  metricsHelp: { recall: string; precision: string }
  stages: Stage[]            // 任务进度的阶段（按事件类型判断走到哪一步）
  verdict: (score: Record<string, any> | null, result: Record<string, any> | null, unverified: number) => Verdict | null
  usesPanels: boolean        // 结果是否按“板”逐块展示（光伏）；救援按发现的人和位置展示
  summary: (site: { arrays?: unknown[]; panels?: unknown[]; landmarks?: unknown[] } | null, counts?: { arrays: number; panels: number }) => string
  planRows: (plan: Record<string, any>) => { label: string; value: string }[]   // 计划卡片显示的条目
}

const POLICY: Record<string, string> = {
  none: '只拍整列照片，不做近距复核', flagged: '只复核初筛判为缺陷的板', flagged_and_unsure: '复核疑似缺陷和看不清的板', all: '每一块板都近距复核',
}

const pct = (v: unknown) => (typeof v === 'number' ? `${Math.round(v * 100)}%` : '—')

// 飞控诊断的阶段与场景无关
export const DIAGNOSIS_STAGES: Stage[] = [
  { key: 'connect', label: '连接飞控', kinds: ['step'] }, { key: 'baseline', label: '基线试飞', kinds: ['baseline', 'inject'] },
  { key: 'compare', label: '对比参数', kinds: ['baseline_result'] }, { key: 'experiment', label: '对照实验', kinds: ['hypothesis', 'experiment_result'] },
  { key: 'conclusion', label: '结论', kinds: ['conclusion'] }, { key: 'reset', label: '恢复参数', kinds: ['reset'] },
]

const solarHelp = {
  recall: '检出率：场地里真实存在的缺陷，找到了多少。低于 100% 说明有漏检，需要复检。',
  precision: '精确率：报出来的缺陷里，有多少是真的。低于 100% 说明有误报，会让运维白跑。',
}

const solar: Scenario = {
  id: 'solar', label: '光伏巡检', verb: '巡检', target: '缺陷', group: '列', item: '板',
  findingTypes: { crack: '裂纹', soiling: '污损', broken: '玻璃破碎', missing: '缺失' },
  legend: [
    { color: 'var(--map-panel)', label: '光伏板' }, { color: 'var(--bad)', label: '确认缺陷' }, { color: '#e3a008', label: '待确认' },
    { color: '#5b8def', label: '复核后排除' }, { color: 'var(--map-route)', label: '计划航线（空心圆为绕行点）', shape: 'dash' },
    { color: '#d6336c', label: '标准答案（虚线框）', shape: 'dash' },
  ],
  examples: (site) => {
    const ids = site?.arrays.map((a) => a.id) ?? []
    const two = ids.length >= 2 ? `${ids[0]} 和 ${ids[ids.length - 1]}` : ids[0] ?? 'A'
    return [
      { text: `检查 ${two} ${ids.length >= 2 ? '两' : ''}列有没有破损或污损`, note: '普通任务：复核可疑的板' },
      { text: '全面仔细检查所有光伏板，一块都不要漏，重点看裂纹', note: '细致任务：每块板近距复核，电量不够自动分架次' },
      { text: `快速扫一遍 ${ids[0] ?? 'A'} 列，看看有没有明显的问题`, note: '快速任务：只看整列照片' },
      { text: '电量保留 40%，检查所有列', note: '指定保留电量' },
      { text: '飞到禁飞区里面拍几张照片', note: '越权请求：会被安全员拒绝' },
    ]
  },
  metricsHelp: solarHelp,
  usesPanels: true,
  planRows: (p) => [
    { label: '巡检的列', value: (p.targets ?? []).join('、') }, { label: '复核策略', value: POLICY[p.closeup_policy] ?? p.closeup_policy },
    { label: '拍摄距离', value: `${p.standoff_m} 米` },
    ...(p.sorties ?? []).map((s: string[], i: number) => ({ label: i ? '' : '架次', value: `第 ${i + 1} 架次：${s.join('、')} 列，耗电约 ${p.estimated_battery?.[i]?.toFixed(0)}%` })),
    { label: '预计时间', value: `${p.estimated_minutes} 分钟` },
  ],
  summary: (site, c) => `${c?.arrays ?? site?.arrays?.length ?? 0} 列 · ${c?.panels ?? site?.panels?.length ?? 0} 块板`,
  verdict: (s, _result, unverified) => {
    if (s && s.defects != null) {
      const found = Math.round((s.recall ?? 0) * s.defects)
      const missed = s.defects - found
      const fa = s.false_alarms?.length ?? 0
      const wrong = s.wrong_type?.length ?? 0
      const metrics = [{ label: '检出率', value: pct(s.recall), help: solarHelp.recall }, { label: '精确率', value: pct(s.precision), help: solarHelp.precision },
        { label: '类型正确', value: pct(s.type_accuracy) }]
      if (missed === 0 && fa === 0) return { tone: 'ok', title: `达标：${s.defects} 个真实缺陷全部找到，没有误报`, metrics,
        advice: wrong ? `其中 ${wrong} 个类型判断有误（见“对照标准答案”），处理前请看照片确认类型。` : '' }
      if (missed > 0) return { tone: 'bad', title: `未达标：${s.defects} 个真实缺陷只找到 ${found} 个，漏检 ${missed} 个`, metrics,
        advice: '建议复检：用“全面仔细检查”（每块板都近距复核）重新执行，或在设置里开启变焦分段拍摄。' }
      return { tone: 'warn', title: `全部找到，但有 ${fa} 个误报`, advice: '误报会让运维白跑，可以看下面的误报照片判断原因。', metrics }
    }
    if (unverified > 0) return { tone: 'warn', title: `完成，但有 ${unverified} 个疑似没有确认`, advice: '请到现场或用照片人工确认。', metrics: [] }
    return { tone: 'ok', title: '完成', advice: '', metrics: [] }
  },
  stages: [
    { key: 'brief', label: '理解任务', kinds: ['brief', 'clarify', 'unsupported'] },
    { key: 'plan', label: '规划与安全校验', kinds: ['plan', 'violation', 'approved'] },
    { key: 'confirm', label: '确认', kinds: ['awaiting_approval'] },
    { key: 'fly', label: '起飞与拍摄', kinds: ['takeoff', 'goto', 'photo', 'segments'] },
    { key: 'judge', label: '初筛与复核', kinds: ['screen', 'closeups', 'verify', 'no_closeup'] },
    { key: 'report', label: '返航与报告', kinds: ['return', 'landed', 'report'] },
  ],
}


const STATE_LABELS: Record<string, string> = { walking: '走动', sitting: '静止', water: '落水' }
const rescue: Scenario = {
  id: 'rescue', label: '应急救援', verb: '搜救', target: '人员', group: '区域', item: '目标',
  findingTypes: { target: '确认目标', person: '待人工辨认', rejected: '确认后排除', bystander: '路人', object: '非人物体', unsure: '看不清', candidate: '疑似' },
  legend: [
    { color: '#4dabf7', label: '水面' }, { color: '#e03131', label: '确认的目标', shape: 'circle' }, { color: '#f59f00', label: '疑似或排除', shape: 'circle' },
    { color: '#868e96', label: '路人或物体', shape: 'circle' }, { color: 'var(--map-route)', label: '搜索航线', shape: 'dash' },
    { color: '#7048e8', label: '地标（最后出现位置）', shape: 'circle' },
  ],
  examples: (site) => {
    const lm = site?.landmarks ?? []
    const at = lm.find((l) => l.id === 'pier')?.label ?? lm[0]?.label ?? '东门'
    return [
      { text: `公园里有个小孩走丢了，大约 8 岁，穿红色上衣、蓝色裤子，半小时前在${at}附近`, note: '标准搜救：从最后出现的位置向外搜' },
      { text: `有位老人在${lm[1]?.label ?? at}附近走失，穿白色上衣、灰色裤子，可能已经离开很久`, note: '时间久：搜索半径会更大' },
      { text: `有人看到一个穿黄色上衣的人在${at}落水了，快找`, note: '紧急：优先水边' },
      { text: `有个人在${at}附近走丢了，长什么样不清楚`, note: '特征不明：找出所有人交给人工辨认' },
      { text: '帮我找个人', note: '信息不够：调度员会追问特征和位置' },
    ]
  },
  metricsHelp: { recall: '找到：确认位置距目标真实位置 5 米以内。', precision: '错认：把路人或地上的衣物确认为目标。' },
  usesPanels: false,
  planRows: (p) => [
    { label: '搜索中心', value: p.last_seen_label ?? '' }, { label: '搜索方式', value: p.pattern === 'parallel' ? '平行扫描' : '扩展方形（从中心向外）' },
    { label: '搜索半径', value: `${p.radius_m} 米` }, { label: '飞行高度', value: `${p.altitude_m} 米` },
    { label: '拍摄点', value: `${p.photos} 个` }, { label: '预计', value: `${p.estimated_minutes} 分钟，耗电约 ${p.estimated_battery?.[0]?.toFixed(0)}%` },
  ],
  summary: (site) => (site?.landmarks ? `${site.landmarks.length} 个地标 · 有走动的人和路人` : '湖面、步道、走动的人和路人'),
  verdict: (s, result) => {
    if (!result) return null
    const found = result.found
    const metrics: Verdict['metrics'] = []
    if (s?.scenario === 'rescue') {
      metrics.push({ label: '目标状态', value: STATE_LABELS[s.target_state] ?? s.target_state })
      if (s.location_error_m != null) metrics.push({ label: '定位误差', value: `${s.location_error_m} 米`, help: '确认位置与目标当时真实位置的距离' })
      if (s.elapsed_s != null) metrics.push({ label: '发现用时', value: `${Math.floor(s.elapsed_s / 60)} 分 ${s.elapsed_s % 60} 秒` })
      metrics.push({ label: '搜索覆盖', value: pct(s.coverage) })
      if (s.misidentified) return { tone: 'bad', title: `错认：确认的不是要找的人（实际是${s.confused_with === '物体' ? '地上的物体' : '路人'}）`, advice: '需要人工核对确认照片；这次的结论不能用。', metrics }
    }
    if (found) return { tone: found.in_water ? 'bad' : 'ok', title: found.in_water ? '已找到目标：在水中，需立即救援' : '已找到目标', advice: result.advice ?? '', metrics }
    const people = (result.detections ?? []).filter((d: any) => d.kind === 'person').length
    if (result.search_all && people) return { tone: 'warn', title: `特征不明：拍到 ${people} 个人，请人工辨认`, advice: result.advice ?? '', metrics }
    if (result.near_misses?.length) return { tone: 'warn', title: '没有完全符合描述的人：报案描述可能有误', advice: result.advice ?? '', metrics }
    return { tone: 'warn', title: '没有找到目标', advice: result.advice ?? '', metrics }
  },
  stages: [
    { key: 'brief', label: '理解任务', kinds: ['brief', 'clarify', 'unsupported'] },
    { key: 'plan', label: '规划搜索与安全校验', kinds: ['plan', 'violation', 'approved'] },
    { key: 'confirm', label: '确认', kinds: ['awaiting_approval'] },
    { key: 'search', label: '起飞与搜索', kinds: ['takeoff', 'screen'] },
    { key: 'verify', label: '发现与确认', kinds: ['candidate', 'found', 'rejected', 'person', 'unsure'] },
    { key: 'report', label: '返航与报告', kinds: ['return', 'landed', 'report'] },
  ],
}

const REGISTRY: Record<string, Scenario> = { solar, rescue }

export function registerScenario(s: Scenario) { REGISTRY[s.id] = s }
export function scenarioOf(id: string | null | undefined): Scenario { return REGISTRY[id ?? 'solar'] ?? solar }
export function allScenarios(): Scenario[] { return Object.values(REGISTRY) }
