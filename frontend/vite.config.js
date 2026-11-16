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
      // 인증 → auth-server (소셜 로그인 콜백 주소는 3000 으로 등록되어 있어 로그인 자체는 3000 에서 확인)
      '/api/auth': { target: process.env.VITE_AUTH_PROXY || 'http://localhost:3003', changeOrigin: false },
      '/oauth2': { target: process.env.VITE_AUTH_PROXY || 'http://localhost:3003', changeOrigin: false },
      '/login/oauth2': { target: process.env.VITE_AUTH_PROXY || 'http://localhost:3003', changeOrigin: false },
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
