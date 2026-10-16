import request from './request'
import type { ApiResult } from './request'

// 后端 i18n 插件（ENABLE_I18N）提供的语种与词条；未启用时为 404
export interface I18nLocaleInfo {
  supported: string[]
  default: string
}

export function getI18nLocales() {
  return request.get<never, ApiResult<I18nLocaleInfo>>('/api/v1/i18n/locales')
}

export function getI18nMessages(locale: string) {
  return request.get<never, ApiResult<Record<string, string>>>('/api/v1/i18n/messages', {
    params: { locale },
  })
}
