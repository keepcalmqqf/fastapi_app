import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发服务器将后端接口代理到本地 FastAPI 服务，目标地址可用 VITE_API_TARGET 覆盖
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const apiTarget = env.VITE_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/auth': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/user': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/member': {
          target: apiTarget,
          changeOrigin: true,
        },
        '/health': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
  }
})
