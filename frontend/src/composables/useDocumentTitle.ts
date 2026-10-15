const TITLE_SUFFIX = 'FastAPI 全栈脚手架'

/**
 * 页面标题跟随路由 meta.title，替代在路由守卫里手写 document.title。
 * 需在根组件（App.vue）中调用一次，路由切换即自动更新标签页标题。
 */
export function useDocumentTitle() {
  const route = useRoute()

  const title = computed(() => {
    const page = route.meta.title as string | undefined
    return page ? `${String(page)} - ${TITLE_SUFFIX}` : TITLE_SUFFIX
  })

  return useTitle(title)
}
