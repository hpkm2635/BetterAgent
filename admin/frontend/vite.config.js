import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// Admin UI dev server on :8095. API calls are proxied to the Admin backend
// (:8094) so the browser never has to deal with CORS. When the backend has
// ADMIN_SECRET_KEY set, the proxy injects it as X-Admin-Token so the shared
// secret never reaches the browser bundle.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const adminToken = env.ADMIN_SECRET_KEY || ''

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
          configure: (proxy) => {
            proxy.on('proxyReq', (proxyReq) => {
              if (adminToken) proxyReq.setHeader('X-Admin-Token', adminToken)
            })
          },
        },
        '/health': {
          target: 'http://127.0.0.1:8094',
          changeOrigin: true,
        },
      },
    },
  }
})
