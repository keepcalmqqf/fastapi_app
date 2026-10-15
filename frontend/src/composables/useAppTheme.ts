/** 暗色模式状态在 localStorage 中的键名，前缀 admin. 与其余本地存储项保持一致 */
const THEME_KEY = 'admin.color-scheme'

/**
 * 暗色模式：在 <html> 上挂 `dark` 类，与 Element Plus 的暗色变量（dark/css-vars.css）对齐。
 * 无历史记录时跟随系统偏好，用户手动切换后以存储值为准。
 */
export function useAppTheme() {
  const isDark = useDark({
    selector: 'html',
    attribute: 'class',
    valueDark: 'dark',
    valueLight: '',
    storageKey: THEME_KEY,
  })
  const toggleDark = useToggle(isDark)

  return { isDark, toggleDark }
}
