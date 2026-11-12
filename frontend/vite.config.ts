import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * 构建产物由 FastAPI StaticFiles 托管，因此 base 固定为 '/'。
 * 开发期 dev server 把 /api 代理到本地后端。
 */
export default defineConfig({
  base: '/',
  plugins: [react()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
