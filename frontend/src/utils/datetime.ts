// 后端统一以 UTC 写入时间且不带时区偏移（见 app/models/base.utc_now），
// 这里补 Z 后再交给浏览器按本地时区格式化，避免被误读成其他时区。
export function formatTime(value: string | null | undefined): string {
  if (!value) return '-'
  const normalized = /(Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

// 输出后端可直接比较的 UTC ISO 串，时区换算交给浏览器完成
export function toUtcIso(value: Date | null | undefined): string | undefined {
  return value ? value.toISOString() : undefined
}
