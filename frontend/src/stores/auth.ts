import { defineStore } from 'pinia'
import { ref } from 'vue'
import { login as apiLogin, type User } from '../api/auth'
import { getMe } from '../api/user'

const TOKEN_KEY = 'access_token'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(TOKEN_KEY))
  const user = ref<User | null>(null)

  async function login(email: string, password: string) {
    const res = await apiLogin(email, password)
    token.value = res.data.access_token
    localStorage.setItem(TOKEN_KEY, res.data.access_token)
    await fetchMe()
  }

  function logout() {
    token.value = null
    user.value = null
    localStorage.removeItem(TOKEN_KEY)
    location.href = '/login'
  }

  async function fetchMe() {
    const res = await getMe()
    user.value = res.data
  }

  return { token, user, login, logout, fetchMe }
})
