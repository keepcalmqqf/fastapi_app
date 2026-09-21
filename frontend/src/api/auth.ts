import request from './request'
import type { ApiResult } from './request'

export interface Token {
  access_token: string
  token_type: string
}

export interface User {
  id: number
  name: string | null
  email: string | null
  is_active: boolean
}

// 登录，成功后返回令牌
export function login(email: string, password: string) {
  return request.post<never, ApiResult<Token>>('/auth/login', { email, password })
}

// 注册用户
export function register(name: string, email: string, password: string) {
  return request.post<never, ApiResult<User>>('/user/create_user', {
    name,
    email,
    password,
  })
}
