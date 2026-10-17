import { getAppInfo, login as apiLogin, logout as apiLogout } from '../api/auth'
import type { User } from '../api/auth'
import { getMe } from '../api/user'
import { usePermissionStore } from './permission'
import { hasSession, markSession } from '../utils/session'

export const useAuthStore = defineStore('auth', () => {
  // 登录用户信息；令牌只存在于 HttpOnly cookie，前端不感知
  const user = ref<User | null>(null)
  // 是否已尝试过恢复会话（首次导航时调 /user/me）
  const restored = ref(false)
  // 是否仍需初始化首个管理员（由免鉴权的 /api/v1/info 提供）
  const needsBootstrap = ref<boolean | null>(null)

  // 拉取服务初始化状态；接口免鉴权，失败时保持未知（null）而不阻塞导航
  async function checkBootstrap() {
    try {
      const res = await getAppInfo()
      needsBootstrap.value = res.data.needs_bootstrap
    } catch {
      needsBootstrap.value = null
    }
    return needsBootstrap.value
  }

  async function fetchMe() {
    const res = await getMe()
    user.value = res.data
  }

  async function login(email: string, password: string) {
    await apiLogin(email, password)
    // 已建立会话：记下痕迹，之后刷新页面才会去探测 /user/me
    markSession(true)
    await fetchMe()
    // 登录成功后同步拉取权限（菜单/按钮级鉴权依赖）
    await usePermissionStore().fetchPermissions()
  }

  async function logout() {
    try {
      await apiLogout()
    } catch {
      // 服务端登出失败也清空本地态
    } finally {
      // 会话已结束：清掉痕迹，避免下次进站还去探测 /user/me
      markSession(false)
      user.value = null
      usePermissionStore().reset()
    }
  }

  // 首次导航时尝试用 cookie 会话恢复登录态，401 视为未登录
  async function restore() {
    // 本站从未建立过会话（首次访问 / 已登出 / 换了浏览器）时无需探测：
    // 直接按未登录处理，省掉一次必然 401 的 /user/me 与随之而来的静默刷新
    if (!hasSession()) {
      user.value = null
      restored.value = true
      return
    }
    try {
      await fetchMe()
      // 恢复会话也要拉取权限，供导航守卫与菜单鉴权使用
      await usePermissionStore().fetchPermissions()
    } catch {
      user.value = null
      markSession(false)
    } finally {
      restored.value = true
    }
  }

  return { user, restored, needsBootstrap, checkBootstrap, login, logout, restore, fetchMe }
})
