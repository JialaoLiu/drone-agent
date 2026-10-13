import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发时把 /api 转发到本机 FastAPI（后端固定 8881 端口），含 WebSocket。
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: { '/api': { target: 'http://127.0.0.1:8881', ws: true, rewrite: (path) => path.replace(/^\/api/, '') } },
  },
})
