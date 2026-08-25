import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// Admin UI dev server on :8095. API calls are proxied to the Admin backend
// (:8094) so the browser never has to deal with CORS.
//
// Auth flow: the login page (LoginPage.vue) asks for ADMIN_SECRET_KEY and
// verifies it against the backend directly (browser sends X-Admin-Token).
// The proxy intentionally does NOT inject a token, so the login gate is real:
// without the correct secret the proxied /api requests return 401.
export default defineConfig(({ mode }) => {
  return {
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
  }
})
