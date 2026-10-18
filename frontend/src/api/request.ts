import axios, { AxiosError } from 'axios'
import type { AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { Router } from 'vue-router'
import { useAuthStore } from '../stores/auth'
// i18n 插件：请求带上当前语种，后端（开启 i18n 插件后）按此返回对应语言的 message
import { currentLocale, translate } from '../plugins/i18n'
import { hasSession, markSession } from '../utils/session'

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

// 可选能力的探测接口：404 表示插件未启用，属正常分支，不该弹错误提示。
// 这里写「插件路由前缀」而非具体路径：插件一停，整个 router 都不挂载，前缀下的
// 所有子路径都会 404，逐个列举既冗长又会在加接口时漏掉。
const SILENT_404_PREFIXES = ['/api/v1/i18n', '/api/v1/tasks', '/api/v1/scheduler']

/**
 * 去掉 query 与 hash，只留参与路由匹配的路径部分。
 * 不剥离的话 `?redirect=/api/v1/tasks` 这类参数会让下面的前缀判断误命中。
 */
function requestPath(url?: string): string {
  return (url ?? '').split(/[?#]/)[0]
}

/**
 * 按「路径段」匹配，而不是子串包含。
 *
 * `url.includes(p)` 会把段边界之外的内容也算命中：`/api/v1/tasks` 会吞掉
 * `/api/v1/tasks-archive` 的真实 404，回跳地址之类的查询参数更容易误伤。改成
 * 判「相等」或「以下一段分隔开头」后，只有真正落在前缀下的接口才被静默。
 */
function matchesPath(url: string | undefined, prefixes: string[]): boolean {
  const path = requestPath(url)
  if (!path) return false
  return prefixes.some((p) => path === p || path.startsWith(`${p}/`))
}

function isNoRetryPath(url?: string): boolean {
  return matchesPath(url, NO_RETRY_PATHS)
}

function isSilent404Path(url?: string): boolean {
  return matchesPath(url, SILENT_404_PREFIXES)
}

// 请求拦截器：不再注入 Authorization（令牌在 HttpOnly cookie 中），
// 统一携带 X-Requested-With，后端据此放行 cookie 鉴权的写请求
request.interceptors.request.use((config) => {
  config.headers['X-Requested-With'] = 'XMLHttpRequest'
  // 显式覆盖浏览器默认语言，保证后端返回的 message 与界面语种一致
  config.headers['Accept-Language'] = currentLocale.value
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
        throw new Error(body?.message || translate('会话已过期，请重新登录'))
      }
    })
}

// 刷新失败：清空本地登录态并跳登录页（带 redirect 回跳参数，并发只跳一次）。
// 仅当当前确实停在需要登录的页面上才跳转：导航守卫里恢复会话时 currentRoute 仍是初始的 '/',
// 若不判断就会把 /register 这类免登录页面顶回登录页，并覆盖掉真实目标地址。
function clearSessionAndRedirect(message: string) {
  const authStore = useAuthStore()
  authStore.user = null
  // refresh 都换不出新令牌，说明会话确实没了，清掉痕迹以免后续请求继续徒劳刷新
  markSession(false)
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

// 未认证处理：single-flight 静默刷新，成功后重试原请求（只重试一次）
async function handleUnauthorized(
  error: AxiosError<ApiResult>,
  message: string,
): Promise<unknown> {
  const config = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
  // 刷新后重试仍 401（如账号被停用/主体被撤销）时不再循环刷新，直接按未登录处理
  if (config?._retried) {
    clearSessionAndRedirect(translate('登录已过期，请重新登录'))
    const retriedError = new Error(message) as AuthError
    retriedError.isAuthError = true
    throw retriedError
  }
  if (!refreshPromise) {
    refreshPromise = rawRefresh().finally(() => {
      refreshPromise = null
    })
  }
  try {
    await refreshPromise
  } catch {
    clearSessionAndRedirect(translate('登录已过期，请重新登录'))
    const authError = new Error(message) as AuthError
    authError.isAuthError = true
    throw authError
  }
  // 刷新成功：会话仍然有效，补记痕迹并重试一次
  markSession(true)
  if (config) config._retried = true
  return request(config as AxiosRequestConfig)
}

// 响应拦截器：解包统一响应，失败时统一提示
request.interceptors.response.use(
  (response) => {
    const body = response.data as ApiResult
    if (response.status === 200 && body && body.code === 200) {
      return body as never
    }
    const fallback = translate('请求失败')
    ElMessage.error(body?.message || fallback)
    return Promise.reject(new Error(body?.message || fallback))
  },
  (error: AxiosError<ApiResult>) => {
    const status = error.response?.status
    const message = error.response?.data?.message || error.message || translate('网络异常')
    if (status === 404 && isSilent404Path(error.config?.url)) {
      return Promise.reject(error)
    }
    if (status !== 401) {
      ElMessage.error(message)
      return Promise.reject(error)
    }
    if (!isNoRetryPath(error.config?.url)) {
      // 本地没有任何会话痕迹、且当前也没有登录用户时，401 就是「未登录」：
      // 浏览器不会带 refresh cookie，静默刷新必然再次 401，直接按未登录返回
      if (!hasSession() && !useAuthStore().user) {
        const anonymousError = new Error(message) as AuthError
        anonymousError.isAuthError = true
        return Promise.reject(anonymousError)
      }
      return handleUnauthorized(error, message)
    }
    // refresh 失败时清空登录态并跳转；login 失败停留在当前页（静默，登录页自行展示错误）
    if (matchesPath(error.config?.url, ['/api/v1/auth/refresh'])) {
      clearSessionAndRedirect(translate('登录已过期，请重新登录'))
    }
    const authError = new Error(message) as AuthError
    authError.isAuthError = true
    return Promise.reject(authError)
  },
)

export default request
