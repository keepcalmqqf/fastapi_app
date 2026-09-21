import axios, { AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

// 后端统一响应结构：{"code": 200, "data": ..., "message": "..."}
export interface ApiResult<T = unknown> {
  code: number
  data: T
  message: string
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
    // 未认证：清除令牌并跳转登录页
    if (status === 401) {
      localStorage.removeItem('access_token')
      if (!location.pathname.startsWith('/login')) {
        location.href = '/login'
        return new Promise(() => {})
      }
    }
    ElMessage.error(message)
    return Promise.reject(error)
  },
)

export default request
