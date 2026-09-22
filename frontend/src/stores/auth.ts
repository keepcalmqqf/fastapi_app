import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as apiLogin, logout as apiLogout } from '../api/auth'
import type { User } from '../api/auth'
import { getMe } from '../api/user'

export const useAuthStore = defineStore('auth', () => {
  // 登录用户信息；令牌只存在于 HttpOnly cookie，前端不感知
  const user = ref<User | null>(null)
  // 是否已尝试过恢复会话（首次导航时调 /user/me）
  const restored = ref(false)

  async function fetchMe() {
    const res = await getMe()
    user.value = res.data
  }

  async function login(email: string, password: string) {
    await apiLogin(email, password)
    await fetchMe()
  }

  async function logout() {
    try {
      await apiLogout()
    } catch {
      // 服务端登出失败也清空本地态
    } finally {
      user.value = null
    }
  }

  // 首次导航时尝试用 cookie 会话恢复登录态，401 视为未登录
  async function restore() {
    try {
      await fetchMe()
    } catch {
      user.value = null
    } finally {
      restored.value = true
    }
  }

  return { user, restored, login, logout, restore, fetchMe }
})
