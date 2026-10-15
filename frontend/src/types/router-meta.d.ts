// 扩展路由元信息类型，供侧边栏菜单、面包屑与导航守卫读取。
// 注意：文件名不要叫 router.d.ts，否则 `import router from './router'` 会优先解析到该声明文件。
import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    // 菜单与面包屑标题；不填则该路由不出现在侧边栏
    title?: string
    // 侧边栏图标名（@element-plus/icons-vue 的组件名，已在 main.ts 全局注册）
    icon?: string
    // 是否需要登录才能访问
    requiresAuth?: boolean
  }
}
