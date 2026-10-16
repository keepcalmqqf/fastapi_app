// createApp / createPinia 由 unplugin-auto-import 自动导入
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as ElementPlusIcons from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
// 暗色变量：配合 src/composables/useAppTheme.ts 在 <html> 上挂 dark 类生效
import 'element-plus/theme-chalk/dark/css-vars.css'
import './styles/admin.css'

import App from './App.vue'
import router from './router'
import { setupRequest } from './api/request'
import { registerPermissionDirectives } from './directives/permission'

const app = createApp(App)

// 图标全局注册，菜单配置里可直接用组件名字符串渲染
for (const [name, component] of Object.entries(ElementPlusIcons)) {
  app.component(name, component)
}

setupRequest(router)

// 注册权限控制指令：v-permission / v-role（菜单与按钮级鉴权）
registerPermissionDirectives(app)

app.use(createPinia())
app.use(router)
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
