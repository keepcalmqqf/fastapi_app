import { defineConfig, loadEnv } from 'vite'
import type { ProxyOptions } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'

// 开发服务器将后端接口代理到本地 FastAPI 服务，目标地址可用 VITE_API_TARGET 覆盖
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  // 后端业务接口统一带 /api/v1 前缀（见 app/api/__init__.py 的 API_V1_PREFIX），
  // 与前端页面路由（/login、/users 等）不共前缀，整个 /api 前缀整体代理即可；
  // /health、/metrics 等运维端点留在根路径，不随业务版本演进
  const apiProxy: Record<string, ProxyOptions> = {
    '/api': {
      target: apiTarget,
      changeOrigin: true,
    },
  }

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
