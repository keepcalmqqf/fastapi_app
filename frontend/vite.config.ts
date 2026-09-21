import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发服务器将后端接口代理到本地 FastAPI 服务
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/auth': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/user': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/member': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
