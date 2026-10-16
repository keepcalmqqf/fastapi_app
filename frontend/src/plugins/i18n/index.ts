/**
 * 极简 i18n 插件：与后端 app/plugins/i18n.py 同源（中文原文即键）。

 * 之所以不引 vue-i18n：词条由后端统一维护、结构是扁平字典，这里只需要
 * 「按语种取词 + 占位插值 + 语言切换」，几十行即可，避免为一个插件引入新依赖。
 * 若后续需要复数、日期/数字本地化等能力，再换成 vue-i18n 也只需替换本文件。
 */
import { computed } from 'vue'
import { useStorage } from '@vueuse/core'
import enUS from './locales/en-US'
import zhCN from './locales/zh-CN'

export type I18nLocale = 'zh-CN' | 'en-US'

const STORAGE_KEY = 'admin.locale'

// 语种持久化：刷新后保持用户选择
export const currentLocale = useStorage<I18nLocale>(STORAGE_KEY, 'zh-CN')
export const availableLocales = ref<I18nLocale[]>(['zh-CN', 'en-US'])
export const defaultLocale = computed<I18nLocale>(() => availableLocales.value[0] ?? 'zh-CN')

const catalogs: Record<I18nLocale, Record<string, string>> = {
  'zh-CN': { ...zhCN },
  'en-US': { ...enUS },
}

export function setLocale(locale: I18nLocale): void {
  if (availableLocales.value.includes(locale)) {
    currentLocale.value = locale
  }
}

/** 合并某语种的词条，供后端词条覆盖本地（或运行时增量扩展）。 */
export function setMessages(locale: I18nLocale, messages: Record<string, string>): void {
  catalogs[locale] = { ...catalogs[locale], ...messages }
}

/** 本地词条表（未合并后端词条），用于筛选哪些键需要同步。 */
export function localCatalogFor(locale: I18nLocale): Record<string, string> {
  return catalogs[locale]
}

/** 翻译；缺词条时回退中文原文，再缺则回退键本身。 */
export function translate(key: string, params?: Record<string, string | number>): string {
  let text = catalogs[currentLocale.value]?.[key] ?? catalogs['zh-CN'][key] ?? key
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value))
    }
  }
  return text
}

/** 组件内可直接 `const { t } = useI18n()`。 */
export function useI18n() {
  return {
    t: translate,
    locale: currentLocale,
    locales: availableLocales,
    setLocale,
  }
}

/** 语言切换器用的可读名称。 */
export const localeLabels: Record<I18nLocale, string> = {
  'zh-CN': '简体中文',
  'en-US': 'English',
}
