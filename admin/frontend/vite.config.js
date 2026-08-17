import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Admin UI dev server on :8095. API calls are proxied to the Admin backend
// (:8094) so the browser never has to deal with CORS.
export default defineConfig({
  plugins: [vue()],
  server: {
    host: true, // listen on all interfaces so both localhost and 127.0.0.1 work
    port: 8095,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8094',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8094',
        changeOrigin: true,
      },
    },
  },
})
