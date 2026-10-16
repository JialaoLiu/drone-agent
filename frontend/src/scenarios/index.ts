// 应用场景注册表：不同场景的术语、示例任务、图例、发现类型集中在这里，页面按注册表渲染，不写死某个场景。
// 新增场景时在这里加一项（并在后端提供对应的场景生成、规划与判断），界面其余部分不用改。
import type { Site } from '../types'

export interface Example { text: string; note: string }
export interface Stage { key: string; label: string; kinds: string[] }
export interface LegendItem { color: string; label: string; shape?: 'square' | 'dash' | 'circle' }
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
}

// 飞控诊断的阶段与场景无关
export const DIAGNOSIS_STAGES: Stage[] = [
  { key: 'connect', label: '连接飞控', kinds: ['step'] }, { key: 'baseline', label: '基线试飞', kinds: ['baseline', 'inject'] },
  { key: 'compare', label: '对比参数', kinds: ['baseline_result'] }, { key: 'experiment', label: '对照实验', kinds: ['hypothesis', 'experiment_result'] },
  { key: 'conclusion', label: '结论', kinds: ['conclusion'] }, { key: 'reset', label: '恢复参数', kinds: ['reset'] },
]

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
  metricsHelp: {
    recall: '检出率：场地里真实存在的缺陷，找到了多少。低于 100% 说明有漏检，需要复检。',
    precision: '精确率：报出来的缺陷里，有多少是真的。低于 100% 说明有误报，会让运维白跑。',
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

const REGISTRY: Record<string, Scenario> = { solar }

export function registerScenario(s: Scenario) { REGISTRY[s.id] = s }
export function scenarioOf(id: string | null | undefined): Scenario { return REGISTRY[id ?? 'solar'] ?? solar }
export function allScenarios(): Scenario[] { return Object.values(REGISTRY) }
