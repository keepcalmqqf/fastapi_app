import request from './request'
import type { ApiResult } from './request'

// 角色条目，对应后端 RoleOut（permissions 为该角色拥有的权限点）
export interface Role {
  id: number
  name: string
  code: string
  description: string
  is_system: boolean
  permissions: Permission[]
  created_at: string
  updated_at: string
}

// 权限点条目，对应后端 PermissionOut
export interface Permission {
  id: number
  name: string
  code: string
  resource: string
  action: string
  description: string
}

// 当前登录用户的角色与权限 code，对应后端 UserPermissionsOut
export interface UserPermissions {
  role_codes: string[]
  permission_codes: string[]
}

// 当前登录用户权限（菜单/按钮级鉴权用）
export function getMyPermissions() {
  return request.get<never, ApiResult<UserPermissions>>('/api/v1/auth/permissions')
}

// 角色列表（含权限点）
export function listRoles() {
  return request.get<never, ApiResult<Role[]>>('/api/v1/role/')
}

// 创建角色
export function createRole(data: { name: string; code: string; description?: string }) {
  return request.post<never, ApiResult<Role>>('/api/v1/role/', data)
}

// 更新角色（name / description）
export function updateRole(
  id: number,
  data: { name?: string; description?: string },
) {
  return request.patch<never, ApiResult<Role>>(`/api/v1/role/${id}`, data)
}

// 删除角色（系统内置角色后端会拒绝）
export function deleteRole(id: number) {
  return request.delete<never, ApiResult<null>>(`/api/v1/role/${id}`)
}

// 设置角色权限（整组替换）
export function setRolePermissions(id: number, permissionCodes: string[]) {
  return request.post<never, ApiResult<Role>>(`/api/v1/role/${id}/permissions`, {
    permission_codes: permissionCodes,
  })
}

// 权限点列表
export function listPermissions() {
  return request.get<never, ApiResult<Permission[]>>('/api/v1/permission/')
}

// 创建权限点
export function createPermission(data: {
  name: string
  code: string
  resource?: string
  action?: string
  description?: string
}) {
  return request.post<never, ApiResult<Permission>>('/api/v1/permission/', data)
}

// 获取某用户所属角色
export function getUserRoles(userId: number) {
  return request.get<never, ApiResult<Role[]>>(`/api/v1/user/${userId}/roles`)
}

// 设置用户角色（整组替换）
export function setUserRoles(userId: number, roleCodes: string[]) {
  return request.post<never, ApiResult<Role[]>>(`/api/v1/user/${userId}/roles`, {
    role_codes: roleCodes,
  })
}
