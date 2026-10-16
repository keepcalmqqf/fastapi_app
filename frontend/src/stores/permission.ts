import { getMyPermissions } from '../api/rbac'

// 当前登录用户的权限/角色态：后端 RBAC 的客户端镜像，供菜单与按钮级鉴权使用。
// 仅在登录态有效，登出后清空。
export const usePermissionStore = defineStore('permission', () => {
  // 用户的角色 code 与权限 code 集合
  const roleCodes = ref<string[]>([])
  const permissionCodes = ref<string[]>([])
  // 是否已拉取过（避免并发重复请求）
  const loaded = ref(false)

  async function fetchPermissions() {
    if (!loaded.value) {
      const res = await getMyPermissions()
      roleCodes.value = res.data.role_codes ?? []
      permissionCodes.value = res.data.permission_codes ?? []
      loaded.value = true
    }
  }

  // 重置（登出时调用）
  function reset() {
    roleCodes.value = []
    permissionCodes.value = []
    loaded.value = false
  }

  // 是否拥有指定权限（任一满足，requireAll=false 时）；默认按 code 精确匹配
  function has(code: string, requireAll = true): boolean {
    const perms = permissionCodes.value
    if (requireAll) return perms.includes(code)
    return perms.includes(code)
  }

  // 是否拥有给定权限列表中的任一个
  function hasAny(codes: string[]): boolean {
    if (!codes.length) return true
    return codes.some((c) => permissionCodes.value.includes(c))
  }

  // 是否拥有给定权限列表中的全部
  function hasAll(codes: string[]): boolean {
    if (!codes.length) return true
    return codes.every((c) => permissionCodes.value.includes(c))
  }

  // 路由/菜单/组件级统一入口：超级管理员放行一切。
  // 直接用 has() 判断会让超管在没有显式权限码时被自己的菜单挡在门外
  // （后端断言与按钮指令都是超管放行，这里必须保持一致）。
  function can(code: string): boolean {
    return isSuperAdmin.value || has(code)
  }

  // 是否拥有指定角色（任一满足）
  function hasRole(code: string): boolean {
    return roleCodes.value.includes(code)
  }

  // 超级管理员放行一切
  const isSuperAdmin = computed(() => roleCodes.value.includes('super_admin'))

  return {
    roleCodes,
    permissionCodes,
    loaded,
    fetchPermissions,
    reset,
    has,
    hasAny,
    hasAll,
    hasRole,
    can,
    isSuperAdmin,
  }
})
