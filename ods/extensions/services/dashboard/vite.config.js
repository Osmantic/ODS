import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createDevApiAccess } from './dev-api-access.js'
import { createDevPreviewAccess } from './dev-preview-access.js'

export function createDashboardConfig(apiKey) {
  const access = createDevApiAccess(apiKey)
  const preview = createDevPreviewAccess()
  return {
    plugins: [react(), access.plugin, preview.plugin],
    server: {
      port: 3003,
      strictPort: true,
      host: '127.0.0.1',
      cors: false,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:3002',
          // Preserve browser Host so the API's Origin-vs-Host same-origin check
          // sees the frontend origin; changeOrigin:true rewrites Host and 403s.
          changeOrigin: false,
          configure: access.configureProxy,
        },
        '/pixel-preview': {
          // Installed nginx on 3001 gates and injects the production key; the
          // Vite dev proxy is a pure pass-through with no credential injection.
          target: 'http://127.0.0.1:3001',
          changeOrigin: false,
          xfwd: false,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: false,
      target: 'es2020',
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom', 'react-router-dom'],
            icons: ['lucide-react'],
          },
        },
      },
    },
  }
}

// Keep the key in the Node proxy's closure. Never use VITE_*, define, or
// static proxy headers: those can expose credentials in browser assets/config.
export default defineConfig(({ command }) => createDashboardConfig(
  command === 'serve' ? process.env.DASHBOARD_API_KEY : undefined,
))
