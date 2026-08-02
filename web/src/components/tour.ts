import { createContext, useContext } from 'react'

/**
 * 안내 패널이 '심사위원이 지금 어디까지 왔는지' 알기 위한 공유 상태의 정의.
 *
 * 컨텍스트와 훅만 여기 둔다. provider(TourContext.tsx)와 파일을 나누는 이유는
 * 한 파일이 컴포넌트와 값을 같이 내보내면 Fast Refresh 가 깨지기 때문이다.
 */
export type TourMark = 'create' | 'trades' | 'apis' | 'chat' | 'settings'

export type Tour = {
  /** 도입부(홈 화면 → 스플래시)를 지나 앱에 들어왔는가 */
  booted: boolean
  setBooted: (v: boolean) => void
  /** 어느 화면을 지나왔는가 — 라우트에서 자동으로 채워진다 */
  visited: Set<TourMark>
  /** '지금 일해보기' 로 사이클을 완주한 적이 있는가 */
  ran: boolean
  markRan: () => void
}

export const TourCtx = createContext<Tour | null>(null)

/** provider 밖에서도 안전하게 쓰인다 — 없으면 아무것도 기억하지 않는다. */
export function useTour(): Tour {
  return (
    useContext(TourCtx) ?? {
      booted: false,
      setBooted: () => {},
      visited: new Set<TourMark>(),
      ran: false,
      markRan: () => {},
    }
  )
}

export function markOf(pathname: string): TourMark | null {
  if (pathname === '/bot/new') return 'create'
  if (pathname.endsWith('/trades')) return 'trades'
  if (pathname.endsWith('/apis')) return 'apis'
  if (pathname.endsWith('/chat')) return 'chat'
  if (pathname.endsWith('/settings')) return 'settings'
  return null
}
