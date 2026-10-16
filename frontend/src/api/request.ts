import axios, { AxiosError } from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { Router } from 'vue-router'
import { useAuthStore } from '../stores/auth'

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
// single-flight 刷新：并发 401 共享同一次 refresh 请求
let refreshPromise: Promise<void> | null = null

// 由 main.ts 在创建 router 后注入，供 401 跳转使用
export function setupRequest(r: Router) {
  router = r
}

const request = axios.create({
  baseURL: '/',
  timeout: 10000,
  // 浏览器一律走 HttpOnly cookie 会话，跨域/代理均需携带 cookie
  withCredentials: true,
})

// 刷新与登录请求自身的 401 不再触发静默刷新，直接按未登录处理
// bootstrap 自身失败（如已初始化返回 409）不应触发静默刷新
const NO_RETRY_PATHS = ['/api/v1/auth/refresh', '/api/v1/auth/login', '/api/v1/auth/bootstrap']

function isNoRetryPath(url?: string): boolean {
  return !!url && NO_RETRY_PATHS.some((p) => url.includes(p))
}

// 请求拦截器：不再注入 Authorization（令牌在 HttpOnly cookie 中），
// 统一携带 X-Requested-With，后端据此放行 cookie 鉴权的写请求
request.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest'
  return config
})

// 绕过本实例直接调 refresh，避免再次进入响应拦截器造成递归
function rawRefresh(): Promise<void> {
  return axios
    .post('/api/v1/auth/refresh', null, {
      baseURL: '/',
      timeout: 10000,
      withCredentials: true,
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
    })
    .then((res) => {
      const body = res.data as ApiResult
      if (res.status !== 200 || !body || body.code !== 200) {
        throw new Error(body?.message || '会话已过期，请重新登录')
      }
    })
}

// 刷新失败：清空本地登录态并跳登录页（带 redirect 回跳参数，并发只跳一次）。
// 仅当当前确实停在需要登录的页面上才跳转：导航守卫里恢复会话时 currentRoute 仍是初始的 '/',
// 若不判断就会把 /register 这类免登录页面顶回登录页，并覆盖掉真实目标地址。
function clearSessionAndRedirect(message: string) {
  const authStore = useAuthStore()
  authStore.user = null
  const current = router?.currentRoute.value
  if (router && !redirectingToLogin && current?.name !== 'login' && current?.meta?.requiresAuth) {
    redirectingToLogin = true
    ElMessage.error(message)
    router
      .push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
      .finally(() => {
        redirectingToLogin = false
      })
  }
}

// 未认证处理：single-flight 静默刷新，成功后重试原请求
async function handleUnauthorized(
  error: AxiosError<ApiResult>,
  message: string,
): Promise<unknown> {
  if (!refreshPromise) {
    refreshPromise = rawRefresh().finally(() => {
      refreshPromise = null
    })
  }
  try {
    await refreshPromise
  } catch {
    clearSessionAndRedirect('登录已过期，请重新登录')
    const authError = new Error(message) as AuthError
    authError.isAuthError = true
    throw authError
  }
  return request(error.config as AxiosRequestConfig)
}

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
    if (status !== 401) {
      ElMessage.error(message)
      return Promise.reject(error)
    }
    if (!isNoRetryPath(error.config?.url)) {
      return handleUnauthorized(error, message)
    }
    // refresh 失败时清空登录态并跳转；login 失败停留在当前页（静默，登录页自行展示错误）
    if (error.config?.url?.includes('/api/v1/auth/refresh')) {
      clearSessionAndRedirect('登录已过期，请重新登录')
    }
    const authError = new Error(message) as AuthError
    authError.isAuthError = true
    return Promise.reject(authError)
  },
)

export default request
