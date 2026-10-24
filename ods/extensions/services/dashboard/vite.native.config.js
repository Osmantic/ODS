import {defineConfig} from 'vite'
import {createDashboardConfig} from './vite.config.js'

const config=createDashboardConfig()
export default defineConfig({...config,build:{...config.build,outDir:'dist/native',
  rollupOptions:{...config.build.rollupOptions,input:'native.html'}}})
