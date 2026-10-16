// 界面文字：角色与状态（与具体应用场景无关；场景相关的文字在 scenarios/）
export const ROLE_LABELS: Record<string, string> = {
  coordinator: '调度员', planner: '规划员', safety: '安全员', pilot: '飞手', inspector: '巡检员',
  reporter: '报告员', diagnostician: '诊断员', system: '系统', searcher: '搜索员',
}
export const STATUS_LABELS: Record<string, string> = {
  queued: '排队中', planning: '规划中', awaiting_approval: '等待确认', running: '执行中', done: '已完成', failed: '失败',
  cancelled: '已取消', rejected: '未通过安全校验', clarify: '需要补充', unsupported: '无法执行',
}
export const LIVE_STATUSES = ['queued', 'planning', 'awaiting_approval', 'running']
