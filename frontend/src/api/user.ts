import request from './request'
import type { ApiResult } from './request'
import type { User } from './auth'

// 后端分页结构（app/schemas/page.py）
export interface Page<T> {
  total: number
  items: T[]
  page: number
  page_size: number
}

// 管理端用户条目，对应后端 UserOut（比登录态 User 多时间字段）
export interface AdminUser {
  id: number
  name: string | null
  email: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

// 获取当前登录用户信息
export function getMe() {
  return request.get<never, ApiResult<User>>('/user/me')
}

// 分页获取用户列表（需管理员令牌）
export function listUsers(page: number, pageSize: number) {
  return request.get<never, ApiResult<Page<AdminUser>>>('/user/list', {
    params: { page, page_size: pageSize },
  })
}

// 更新用户（name / is_active，均为可选）
export function updateUser(id: number, data: { name?: string; is_active?: boolean }) {
  return request.patch<never, ApiResult<AdminUser>>(`/user/${id}`, data)
}

// 删除用户（软删除）
export function deleteUser(id: number) {
  return request.delete<never, ApiResult<null>>(`/user/${id}`)
}
