// 接口客户端：统一的请求、访问令牌与资源地址。各领域的接口在同目录的其他文件里。

// 后端设置了 DRONE_AGENT_ACCESS_TOKEN 时，所有请求都要带访问令牌；令牌只保存在本机浏览器
const TOKEN_KEY = 'drone_access_token'

export function accessToken(): string {
  try { return localStorage.getItem(TOKEN_KEY) ?? '' } catch { return '' }
}

// 图片、裁切图、HTML 报告由浏览器直接加载，不能附加请求头；令牌同时写入同源 cookie，后端对这些请求读取 cookie
function syncTokenCookie(token: string) {
  document.cookie = `drone_access_token=${encodeURIComponent(token)}; path=/; SameSite=Strict`
}
if (accessToken()) syncTokenCookie(accessToken())

function askForToken(): boolean {
  const token = window.prompt('后端要求访问令牌，请输入：')?.trim()
  if (!token) return false
  try { localStorage.setItem(TOKEN_KEY, token) } catch { /* 隐私模式下不可写 */ }
  syncTokenCookie(token)
  return true
}

export async function request<T>(url: string, init: RequestInit = {}, retried = false): Promise<T> {
  const response = await fetch(`/api${url}`, { ...init, headers: { ...init.headers, 'X-Access-Token': accessToken(), 'Content-Type': 'application/json' } })
  if (response.status === 401 && !retried && askForToken()) return request<T>(url, init, true)
  const body = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `请求失败（${response.status}）`)
  return body as T
}

export const post = <T>(url: string, body?: unknown) => request<T>(url, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })
export const put = <T>(url: string, body: unknown) => request<T>(url, { method: 'PUT', body: JSON.stringify(body) })
export const enc = encodeURIComponent

// 任务事件的 WebSocket 地址；令牌放在查询参数里（浏览器的 WebSocket 不能自定义请求头）
export function wsUrl(path: string, params: Record<string, string>): string {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const q = new URLSearchParams(params)
  if (accessToken()) q.set('token', accessToken())
  return `${proto}://${location.host}/api${path}?${q}`
}
