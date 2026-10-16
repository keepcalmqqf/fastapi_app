import { computed } from 'vue'
import elementEn from 'element-plus/es/locale/lang/en'
import elementZhCn from 'element-plus/es/locale/lang/zh-cn'
import { currentLocale } from './index'

/** Element Plus 组件库语言：随 i18n 当前语种切换（菜单为空、分页文案等）。 */
export const elementLocale = computed(() =>
  currentLocale.value === 'en-US' ? elementEn : elementZhCn,
)
