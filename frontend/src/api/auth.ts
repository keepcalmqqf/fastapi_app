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
  name: string | null
  email: string | null
  is_active: boolean
}

// 登录，成功后由后端种下 HttpOnly cookie 会话
export function login(email: string, password: string) {
  return request.post<never, ApiResult<TokenOut>>('/auth/login', { email, password })
}

// 刷新会话（读取 cookie，无需 body），响应会轮换并重新种下 cookie
export function refresh() {
  return request.post<never, ApiResult<TokenOut>>('/auth/refresh')
}

// 登出，撤销服务端令牌并清除 cookie
export function logout() {
  return request.post<never, ApiResult<null>>('/auth/logout')
}

// 注册用户（不种 cookie，成功后需再调 login 建立会话）
export function register(name: string, email: string, password: string) {
  return request.post<never, ApiResult<User>>('/user/create_user', {
    name,
    email,
    password,
  })
}
