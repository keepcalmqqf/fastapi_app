import { getI18nLocales, getI18nMessages } from '../../api/i18n'
import type { I18nLocale } from './index'
import { availableLocales, defaultLocale, localCatalogFor, setLocale, setMessages } from './index'

/**
 * 从后端拉取语种与词条。

 * 后端 i18n 插件（ENABLE_I18N）未开启时接口为 404，此时保留本地词条，
 * 前端仍可在 zh-CN / en-US 之间切换，只是译文以本地文件为准。
 */
export async function syncFromBackend(): Promise<boolean> {
  try {
    const localesRes = await getI18nLocales()
    // 后端可能声明前端没有词条的语种，这里只保留已内置词条的那部分
    const supported = (localesRes.data.supported ?? []).filter((item): item is I18nLocale =>
      availableLocales.value.includes(item as I18nLocale),
    )
    if (supported.length) availableLocales.value = supported

    for (const locale of supported) {
      const res = await getI18nMessages(locale)
      const remote = res.data ?? {}
      // 只更新本地已有的键：后端词条是「中文原文 → 译文」，可能含前端用不到的
      // 服务端文案，全量合并会把它们塞进前端词条表
      const merged: Record<string, string> = {}
      for (const [key, value] of Object.entries(remote)) {
        if (key in localCatalogFor(locale)) merged[key] = value
      }
      if (Object.keys(merged).length) setMessages(locale, merged)
    }
    return true
  } catch {
    // 接口不存在或未返回数据：回退本地词条，不阻塞应用启动
    return false
  }
}

/** 初始化：恢复上次选择的语种（持久化在 localStorage）。 */
export function restoreLocale(): void {
  setLocale(defaultLocale.value)
}
