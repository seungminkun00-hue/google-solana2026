import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import springboard from '../assets/ios-springboard.png'
import splash from '../assets/sol-splash.png'
import { useTour } from './tour'
import s from './IntroBoot.module.css'

/**
 * 앱을 켜는 순간부터 보여주는 도입부.
 *
 *   ① iOS 홈 화면 (슈퍼SOL 2.0 아이콘)
 *   ② 아이콘을 누르면 SOL 스플래시가 잠깐
 *   ③ 우리 온보딩 화면
 *
 * [2026-08-03 이미지 교체]
 * 두 이미지 모두 1206 × 2616 = 디자인(402 × 872)의 **정확히 3배**다.
 * 3배로 받은 덕에 목업 안에서 선명하다 — CSS 가 100% 로 줄여 깐다.
 *
 * 아이콘 위치는 원본 픽셀에서 직접 재서 3으로 나눈 값이다.
 *   파란 아이콘   원본 x 90..310 · y 267..487  (221px 정사각) → 디자인 30, 89, 74
 *   라벨 아랫변   원본 y 545                                  → 디자인 182
 * 이미지를 바꾸면 이 값들을 **다시 재야 한다.** 눈으로 어림하면 클릭 영역이
 * 어긋나 아이콘을 눌러도 안 열린다 — 그래서 픽셀에서 찾는다.
 *
 * 새로고침하면 다시 ① 부터 시작한다. 시연에서 매번 처음부터 보여줄 수 있도록
 * 일부러 기억하지 않는다. 앱 안에서는 화면 맨 아래 홈 인디케이터 자리를 눌러
 * 언제든 홈 화면으로 돌아온다.
 */
const ICON = { left: 30, top: 89, size: 74, labelBottom: 182 }

const ZOOM_MS = 240 // 아이콘에서 열리는 연출
const HOLD_MS = 520 // 스플래시가 머무는 시간
const FADE_MS = 240 // 앱으로 넘어가는 페이드

type Phase = 'springboard' | 'splash' | 'app'

export function IntroBoot({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>('springboard')
  const [splashOut, setSplashOut] = useState(false)
  const timers = useRef<number[]>([])
  const nav = useNavigate()
  // 옆의 진행 안내가 1단계('앱을 켭니다')를 끝난 것으로 표시하려면
  // 도입부를 지났다는 사실을 밖에서도 알아야 한다.
  const { setBooted } = useTour()

  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout)
    timers.current = []
  }, [])

  useEffect(() => clearTimers, [clearTimers])

  // 스플래시가 뜨는 순간 처음 보이면 한 박자 늦게 그려진다. 미리 받아둔다.
  useEffect(() => {
    const img = new Image()
    img.src = splash
  }, [])

  function launch() {
    if (phase !== 'springboard') return
    clearTimers()
    setPhase('splash')
    timers.current.push(
      window.setTimeout(() => {
        // 앱을 깔아두고 스플래시만 걷어낸다 (교차 전환)
        setPhase('app')
        setBooted(true)
        setSplashOut(true)
        timers.current.push(
          window.setTimeout(() => setSplashOut(false), FADE_MS),
        )
      }, ZOOM_MS + HOLD_MS),
    )
  }

  function goHome() {
    clearTimers()
    setSplashOut(false)
    setPhase('springboard')
    setBooted(false)
    nav('/', { replace: true }) // 다시 켜면 온보딩부터
  }

  const showSpringboard = phase !== 'app'
  const showSplash = phase === 'splash' || splashOut

  return (
    <div className={s.root}>
      {phase === 'app' && children}

      {showSpringboard && (
        <div className={s.layer}>
          <img className={s.shot} src={springboard} alt="아이폰 홈 화면" />
          <button
            type="button"
            className={s.appIcon}
            style={{
              left: ICON.left - 6,
              top: ICON.top - 6,
              width: ICON.size + 12,
              height: ICON.labelBottom - ICON.top + 6,
            }}
            onClick={launch}
            aria-label="슈퍼SOL 2.0 열기"
          />
          <span
            className={s.hint}
            style={{ top: ICON.labelBottom + 14, left: ICON.left - 14 }}
            aria-hidden
          >
            눌러서 실행
          </span>
        </div>
      )}

      {showSplash && (
        <div
          className={`${s.layer} ${s.splash} ${splashOut ? s.splashOut : ''}`}
          style={{
            // iOS 처럼 아이콘 자리에서 열린다
            transformOrigin: `${ICON.left + ICON.size / 2}px ${
              ICON.top + ICON.size / 2
            }px`,
          }}
        >
          <img className={s.shot} src={splash} alt="" />
        </div>
      )}

      {/* 앱 안에서 홈 화면으로 돌아가는 자리. 실제 아이폰의 홈 인디케이터를
          누르는 것과 같은 위치라, 시연 중 처음부터 다시 보여줄 수 있다. */}
      {phase === 'app' && (
        <button
          type="button"
          className={s.homeIndicator}
          onClick={goHome}
          aria-label="홈 화면으로"
          title="홈 화면으로 (도입부 다시 보기)"
        />
      )}
    </div>
  )
}
