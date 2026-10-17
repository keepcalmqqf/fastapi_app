// 会话痕迹：令牌存放在 HttpOnly Cookie 中，前端读不到，无法直接判断「是否登录过」。
// 这里用一个本地标记记录本站是否曾建立过会话，供两类场景使用：
//   1. 会话恢复（router 守卫里的 /user/me）在无痕迹时直接跳过，避免必然 401 的探测请求；
//   2. 401 拦截器在无痕迹时不做静默刷新（没有 refresh cookie 可换发，刷新必然失败）。
// 该标记只影响「要不要发请求」，不参与任何鉴权判断，伪造它无法获得访问权限。
const KEY = 'auth.has_session'

export function hasSession(): boolean {
  try {
    return localStorage.getItem(KEY) === '1'
  } catch {
    // 隐私模式 / 存储被禁用时按无痕迹处理，退化为每次都探测
    return false
  }
}

export function markSession(active: boolean): void {
  try {
    if (active) {
      localStorage.setItem(KEY, '1')
    } else {
      localStorage.removeItem(KEY)
    }
  } catch {
    // 存储不可用时忽略，仅失去「跳过探测」的优化
  }
}
