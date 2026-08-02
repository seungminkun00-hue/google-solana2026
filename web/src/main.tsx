// ★ 반드시 첫 줄. @solana/web3.js 가 쓰는 Buffer 를 심는다.
import './polyfills'

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
