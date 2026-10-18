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
    // 先确认会话可用，再记下痕迹：此前先 markSession(true) 再 fetchMe()，
    // 后者失败时界面报「登录失败」，可 Cookie 已建立、痕迹也已留下——
    // 表现为「登录报错但刷新一下就好了」。这里让状态与界面提示保持一致。
    try {
      await fetchMe()
    } catch (err) {
      markSession(false)
      throw err
    }
    markSession(true)
    // 权限拉取失败不应让登录失败：会话已建立，权限置空即可——菜单/按钮按无权限
    // 渲染，用户可在控制台刷新重试，总好过卡在登录页却已经登录了。
    try {
      await usePermissionStore().fetchPermissions()
    } catch {
      usePermissionStore().reset()
    }
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
    // /user/me 失败才是「会话确实失效」；权限拉取失败不是。
    // 此前两者共用一个 catch：一次权限接口抖动（Redis 抖动、RBAC 未补种导致的 403）
    // 就会把已登录用户判成未登录，而且清掉会话痕迹后下次进站连探测都不做，
    // 用户必须手动重新登录——可 Cookie 会话明明一直有效。
    try {
      await fetchMe()
    } catch {
      user.value = null
      markSession(false)
      restored.value = true
      return
    }
    try {
      // 恢复会话也要拉取权限，供导航守卫与菜单鉴权使用
      await usePermissionStore().fetchPermissions()
    } catch {
      // 权限置空即可：导航守卫会按缺权限处理，不会把人踢出去
      usePermissionStore().reset()
    }
    restored.value = true
  }

  return { user, restored, needsBootstrap, checkBootstrap, login, logout, restore, fetchMe }
})
