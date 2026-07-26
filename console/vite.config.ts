import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// 构建产物由 FastAPI 挂载在 /console/;开发时代理到本地网关
export default defineConfig({
  base: '/console/',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/admin': { target: 'http://127.0.0.1:8080', ws: true },
      '/healthz': 'http://127.0.0.1:8080',
      '/metrics': 'http://127.0.0.1:8080',
    },
  },
})
