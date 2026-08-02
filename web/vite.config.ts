import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 백엔드는 uvicorn app.main:app --port 8100 로 뜬다.
// CORS가 "*" 라 직접 호출해도 되지만, 프록시를 쓰면 프론트 코드가
// 포트를 몰라도 되고 배포할 때 이 파일만 바꾸면 된다.
const BACKEND = process.env.BACKEND_ORIGIN ?? 'http://127.0.0.1:8100'

export default defineConfig({
  plugins: [react()],

  build: {
    // 아이콘 SVG를 전부 data: URI 로 인라인한다.
    //
    // 기본값(4KB)을 넘는 SVG는 별도 파일로 빠지고, CSS 안의 그 경로가
    // 상대 경로로 해석되면서 /bot/bot2 같은 중첩 라우트에서 404가 났다
    // (설정 기어 22KB가 아예 안 보이던 원인). data: URI 는 해석할 경로가
    // 없어서 어느 라우트에서든, 어떤 base 로 배포하든 똑같이 동작한다.
    //
    // 가장 큰 아이콘이 22KB 라 넉넉히 40KB 로 잡는다. 아이폰 목업 PNG는
    // TSX 에서 import 하므로 이 값과 무관하게 파일로 남는다.
    assetsInlineLimit: 40 * 1024,
  },

  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
