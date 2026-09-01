import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // 显式绑定 IPv4：macOS 下默认只监听 ::1，会导致 127.0.0.1 无法访问
    host: '127.0.0.1',
    // 用独立端口，避免与机器上其它 Vite 项目（默认 5173）冲突
    port: 5180,
    strictPort: false,
    proxy: {
      // 后端接口代理；SSE 由后端直连时需注意 Nginx 关闭缓冲（见 deploy/nginx）
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ['react', 'react-dom', 'react-router-dom'],
          antd: ['antd', '@ant-design/icons'],
          echarts: ['echarts', 'echarts-for-react'],
          markdown: ['react-markdown', 'remark-gfm'],
        },
      },
    },
  },
});
