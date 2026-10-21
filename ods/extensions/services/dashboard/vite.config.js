import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createDevApiAccess } from './dev-api-access.js'

export function createDashboardConfig(apiKey) {
  const access = createDevApiAccess(apiKey)
  return {
    plugins: [react(), access.plugin],
    server: {
      port: 3001,
      strictPort: true,
      host: '127.0.0.1',
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:3002',
          // Preserve browser Host so the API's Origin-vs-Host same-origin check
          // sees the frontend origin; changeOrigin:true rewrites Host and 403s.
          changeOrigin: false,
          configure: access.configureProxy,
        }
      }
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
export default defineConfig(({ command, isPreview }) => createDashboardConfig(
  command === 'serve' && !isPreview ? process.env.DASHBOARD_API_KEY : undefined,
))
