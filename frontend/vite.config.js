import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 개발 서버 프록시 (배포 시 nginx.conf 와 같은 규칙)
//   /api/ocr/* → ocr_backend (OCR 인식·확인·저장)
//   그 외 /api/* → rag_backend (챗봇 SSE, 거래 목록/삭제)
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api/ocr': {
        target: process.env.VITE_OCR_PROXY || 'http://localhost:3002',
        changeOrigin: true,
      },
      '/api': {
        target: process.env.VITE_RAG_PROXY || 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
})
