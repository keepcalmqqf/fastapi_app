import request from './request'
import type { ApiResult } from './request'
import type { User } from './auth'

// 获取当前登录用户信息
export function getMe() {
  return request.get<never, ApiResult<User>>('/user/me')
}
