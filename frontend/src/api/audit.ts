import request from './request'
import type { ApiResult } from './request'
import type { Page } from './user'

// 审计日志条目，对应后端 AuditLogOut（app/schemas/audit.py）
export interface AuditLog {
  id: number
  // 操作者身份：admin / member / system
  actor_type: string
  actor_id: number | null
  action: string
  target_type: string
  target_id: string | null
  // 变更要点，后端存的是 JSON 字符串
  detail: string | null
  ip: string | null
  // 后端按 UTC 写入且不带偏移（见 app/models/base.utc_now），
  // 这里补 Z 再交给浏览器格式化，避免被当成 UTC+0 之外的时区解读
  created_at: string
}

export interface AuditLogParams {
  page: number
  page_size: number
  action?: string
  actor_id?: number
  created_at_from?: string
  created_at_to?: string
}

// 分页查询审计日志（需管理员令牌）
export function listAuditLogs(params: AuditLogParams) {
  return request.get<never, ApiResult<Page<AuditLog>>>('/audit/logs', { params })
}
