import {
  useCallback, useEffect, useMemo, useState, type ReactNode,
} from 'react'
import { useLocation } from 'react-router-dom'
import { markOf, TourCtx, type TourMark } from './tour'

/**
 * 안내 패널과 앱 화면 사이의 다리.
 *
 * 안내 패널은 아이폰 목업 **바깥**에 있고 앱 화면은 목업 **안**에 있다.
 * 둘은 DeviceFrame 에서 형제로 렌더되므로 부모를 통하지 않으면 서로를
 * 볼 수 없다. 그래서 DeviceFrame 을 감싸는 자리에 이 provider 를 둔다.
 *
 * 새로고침하면 처음부터 다시 시작한다 — 도입부(IntroBoot)가 그렇게
 * 동작하므로 안내도 같이 맞춰야 둘이 어긋나지 않는다.
 */
export function TourProvider({ children }: { children: ReactNode }) {
  const [booted, setBooted] = useState(false)
  const [visited, setVisited] = useState<Set<TourMark>>(() => new Set())
  const [ran, setRan] = useState(false)
  const { pathname } = useLocation()

  useEffect(() => {
    const mark = markOf(pathname)
    if (!mark) return
    setVisited((prev) => (prev.has(mark) ? prev : new Set(prev).add(mark)))
  }, [pathname])

  const markRan = useCallback(() => setRan(true), [])

  const value = useMemo(
    () => ({ booted, setBooted, visited, ran, markRan }),
    [booted, visited, ran, markRan],
  )
  return <TourCtx.Provider value={value}>{children}</TourCtx.Provider>
}
