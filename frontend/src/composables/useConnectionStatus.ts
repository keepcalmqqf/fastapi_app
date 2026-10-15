/**
 * 网络与窗口焦点状态。
 * 只有"联网且窗口聚焦"时才认为页面活跃，供轮询类页面暂停请求、顶栏提示离线使用。
 */
export function useConnectionStatus() {
  const online = useOnline()
  const focused = useWindowFocus()

  const active = computed(() => online.value && focused.value)

  return { online, focused, active }
}
