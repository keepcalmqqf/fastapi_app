import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'

// 开发服务器将后端接口代理到本地 FastAPI 服务，目标地址可用 VITE_API_TARGET 覆盖
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  // 代理前缀必须带路径边界：字符串写法是「前缀匹配」，会把 /users、/members、
  // /audit-logs 这些前端页面也转发给后端，拿回来的就是后端托管的旧 dist。
  // 这里限定为「整段相等」或「其后紧跟 /」，避免页面路径被误代理。
  const apiPrefixes = ['/auth', '/user', '/member', '/audit', '/health']
  const apiProxy = apiPrefixes.map((prefix) => ({
    context: (path: string) => path === prefix || path.startsWith(`${prefix}/`),
    target: apiTarget,
    changeOrigin: true,
  }))

  return {
    plugins: [
      vue(),
      // 自动导入：vue / vue-router / pinia 的响应式 API 与 VueUse 组合式函数免 import；
      // src/composables 下的项目自定义 composable 同样免 import
      AutoImport({
        imports: ['vue', 'vue-router', 'pinia', '@vueuse/core'],
        dirs: ['src/composables'],
        // 类型声明落在 src 下，已被 tsconfig 的 include 覆盖，
        // 保证 vue-tsc（build 阶段先于 vite 执行）也能识别这些全局 API
        dts: 'src/auto-imports.d.ts',
        // <template> 中也可直接使用自动导入的 API
        vueTemplate: true,
      }),
    ],
    server: {
      proxy: apiProxy,
    },
  }
})
