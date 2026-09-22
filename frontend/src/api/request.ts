import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'
import type { Router } from 'vue-router'

// 后端统一响应结构：{"code": 200, "data": ..., "message": "..."}
export interface ApiResult<T = unknown> {
  code: number
  data: T
  message: string
}

// 可识别的认证错误：调用方可通过 isAuthError 判断，finally 能正常执行
export interface AuthError extends Error {
  isAuthError: true
}

let router: Router | null = null
// 并发多个请求同时 401 时，保证只跳转一次
let redirectingToLogin = false

// 由 main.ts 在创建 router 后注入，供 401 跳转使用
export function setupRequest(r: Router) {
  router = r
}

const request = axios.create({
  baseURL: '/',
  timeout: 10000,
})

// 请求拦截器：携带 Bearer Token
request.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器：解包统一响应，失败时统一提示
request.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResult
    if (response.status === 200 && body && body.code === 200) {
      return body as never
    }
    ElMessage.error(body?.message || '请求失败')
    return Promise.reject(new Error(body?.message || '请求失败'))
  },
  (error: AxiosError<ApiResult>) => {
    const status = error.response?.status
    const message = error.response?.data?.message || error.message || '网络异常'
    // 未认证：清除令牌并跳转登录页（带 redirect 回跳参数，并发 401 只跳一次）
    if (status === 401) {
      localStorage.removeItem('access_token')
      const authError = new Error(message) as AuthError
      authError.isAuthError = true
      if (router && !redirectingToLogin && router.currentRoute.value.name !== 'login') {
        redirectingToLogin = true
        ElMessage.error(message)
        router
          .push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
          .finally(() => {
            redirectingToLogin = false
          })
      }
      return Promise.reject(authError)
    }
    ElMessage.error(message)
    return Promise.reject(error)
  },
)

export default request
