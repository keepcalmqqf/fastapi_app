import request from './request'
import type { ApiResult } from './request'
import type { Page } from './user'

// 会员条目，对应后端 MemberOut
export interface Member {
  id: number
  nickname: string | null
  email: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

// 分页获取会员列表（后台运营接口，需管理员令牌）
export function listMembers(page: number, pageSize: number) {
  return request.get<never, ApiResult<Page<Member>>>('/member/list', {
    params: { page, page_size: pageSize },
  })
}
