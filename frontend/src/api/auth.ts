import request from './request'
import type { ApiResult } from './request'

export interface TokenOut {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface User {
  id: number
  name: string
  email: string
  is_active: boolean
}

// 登录，成功后由后端种下 HttpOnly cookie 会话
export function login(email: string, password: string) {
  return request.post<never, ApiResult<TokenOut>>('/api/v1/auth/login', { email, password })
}

// 刷新会话（读取 cookie，无需 body），响应会轮换并重新种下 cookie
export function refresh() {
  return request.post<never, ApiResult<TokenOut>>('/api/v1/auth/refresh')
}

// 登出，撤销服务端令牌并清除 cookie
export function logout() {
  return request.post<never, ApiResult<null>>('/api/v1/auth/logout')
}

// 初始化首个管理员（空库自举专用，不种 cookie，成功后需再调 login 建立会话）。
// 与「创建后台用户」是两件事：后者需要管理员令牌与 user:write 权限
export function bootstrapAdmin(name: string, email: string, password: string) {
  return request.post<never, ApiResult<User>>('/api/v1/auth/bootstrap', {
    name,
    email,
    password,
  })
}

// 服务基础信息：needs_bootstrap 表示是否仍需初始化首个管理员（免鉴权）
export interface AppInfo {
  service: string
  docs: string
  needs_bootstrap: boolean
}

export function getAppInfo() {
  return request.get<never, ApiResult<AppInfo>>('/api/v1/info')
}
