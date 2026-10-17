import axios from 'axios'
import request from './request'
import type { ApiResult } from './request'

// 异步任务控制台的数据来自两个可选插件的观测接口：
//   GET  /api/v1/tasks            → 运行状态 + 最近任务（需 task:read）
//   GET  /api/v1/scheduler        → 定时计划（需 task:read）
//   POST /api/v1/tasks/dispatch   → 手动投递具名任务（需 task:write）
// 插件未启用时路由根本不存在（404）。这里把 404 转成显式的「插件未启用」信号，
// 交给页面渲染空态，而不是当成请求失败弹错误提示。

/** 任务状态取值：memory 用 pending/running/success/failed，redis 用 queued/success/failed */
export type TaskState = 'queued' | 'pending' | 'running' | 'success' | 'failed' | 'unknown'

export interface TaskJob {
  // redis 后端只有 job_id；memory 后端同时给出 task_id（同一个值，保留兼容）
  job_id: string
  task_id?: string
  name?: string
  state?: TaskState
  attempt?: string
  enqueued_at?: string
  started_at?: string
  finished_at?: string
  error?: string
  last_error?: string
  result?: string
}

export interface TaskStatus {
  enabled: boolean
  // memory = 进程内队列 + worker 协程；redis = Redis Streams 消费组（分布式）
  backend: 'memory' | 'redis'
  registered: string[]
  // memory 后端：常驻 worker 数、队列长度、执行中条数、内存里留存的记录数
  workers: number
  queue_size: number
  running: number
  tracked: number
  // redis 后端：Stream 长度（含已消费未修剪）、待处理（已派发未 ACK）、消费者数
  queue_len?: number
  pending?: number
  consumers?: number
  stream?: string
  group?: string
  recent: TaskJob[]
  // 传 job_id 查询时才有
  job?: TaskJob
}

export interface ScheduledJob {
  name: string
  cron: string
  next_run_at: string | null
  last_run_at: string | null
  last_job_id: string | null
}

export interface SchedulerStatus {
  enabled: boolean
  timezone: string
  jobs: ScheduledJob[]
}

export interface TaskDispatchPayload {
  name: string
  args?: unknown[]
  kwargs?: Record<string, unknown>
  retries?: number
}

export interface TaskDispatchResult {
  name: string
  job_id: string
}

/** 插件未启用（接口 404）。页面据此渲染「未启用」空态。 */
export class PluginDisabledError extends Error {
  name = 'PluginDisabledError'
  plugin: string

  constructor(plugin: string) {
    super(`插件未启用: ${plugin}`)
    this.plugin = plugin
  }
}

function isNotFound(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 404
}

/** 任务运行状态 + 最近任务；传 jobId 时返回该任务的单条状态。 */
export async function getTaskStatus(params: { limit?: number; jobId?: string } = {}) {
  try {
    return await request.get<never, ApiResult<TaskStatus>>('/api/v1/tasks', {
      params: {
        limit: params.limit ?? 20,
        job_id: params.jobId || undefined,
      },
    })
  } catch (error) {
    if (isNotFound(error)) throw new PluginDisabledError('tasks')
    throw error
  }
}

/** 定时计划列表。 */
export async function getSchedulerJobs() {
  try {
    return await request.get<never, ApiResult<SchedulerStatus>>('/api/v1/scheduler')
  } catch (error) {
    if (isNotFound(error)) throw new PluginDisabledError('scheduler')
    throw error
  }
}

/** 手动投递一个已注册的具名任务，返回 job_id。 */
export function dispatchTask(payload: TaskDispatchPayload) {
  return request.post<never, ApiResult<TaskDispatchResult>>('/api/v1/tasks/dispatch', payload)
}
