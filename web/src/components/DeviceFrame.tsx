import { useEffect, useState, type ReactNode } from 'react'
import bezel from '../assets/iphone-frame.png'
import { StatusLamp } from './StatusLamp'
import s from './DeviceFrame.module.css'

/**
 * 아이폰 목업 안에서 앱이 돌아가게 하는 껍데기.
 *
 * [목업 PNG 실측]
 *   전체 900 × 1840, 화면 구멍은 안쪽이 **투명**이다.
 *   구멍  x 48..851 (804)   y 46..1793 (1748)
 *   → 구멍이 디자인 프레임(402 × 874)의 정확히 2배다.
 *
 * 그래서 PNG를 0.5배(450 × 920)로 놓으면 구멍이 402 × 874 가 되어
 * 앱이 1:1 로 들어간다. 확대·축소가 없으니 글자가 흐려지지 않는다.
 *   화면 위치 = left 24 (48÷2), top 23 (46÷2)
 *
 * 상태바(9:41·신호·배터리)와 홈 인디케이터는 PNG에 그려져 있다.
 * 디자인이 상단 74px 를 비워둔 덕에 겹치지 않는다.
 *
 * [스크롤]
 * 페이지 자체는 스크롤되지 않는다(body overflow:hidden). 스크롤은 화면
 * 안쪽 .scroll 에서만 일어나므로, 헤더처럼 고정이어야 할 것과 하단 탭은
 * 그대로 붙어 있는다.
 */
const DEVICE_W = 450
const DEVICE_H = 920
const SCREEN_X = 24
const SCREEN_Y = 23
const SCREEN_W = 402
const SCREEN_H = 874

/* 3단 배치: [폰] [안내 또는 지갑시연] [실행 로그]
   가운데는 Sidebar 가 탭으로 갈아끼우고(GuidePanel·JudgePanel 둘 다 400px),
   오른쪽 로그(ActivityPanel.module.css 의 .panel)는 탭과 무관하게 늘 있다.
   여기가 실제보다 크면 폰이 필요 이상으로 줄고, 작으면 겹친다. */
const CONTROL_W = 400 // 가운데 기둥 고정폭
const LOG_MIN_W = 340 // 실행 로그 최소폭 (남는 폭은 로그가 가져간다)
const GAP = 24
const ASIDE_RESERVE = CONTROL_W + LOG_MIN_W + GAP * 2

/* 이 아래로는 3단이 성립하지 않는다. 폰을 아무리 줄여도 두 기둥이
   최소폭을 못 지키기 때문. */
const ROW_MIN_W = 1150

/**
 * 어떤 배치로 세울지 정한다.
 *   row   — 폰·조작부·로그 3단. 시연용 기본 배치.
 *   stack — 폭이 모자라 세로로 쌓는다. 이때는 페이지가 스크롤된다.
 *   bare  — 실제 폰. 목업도 패널도 없이 앱만 꽉 채운다.
 */
function layoutFor(aside: boolean): 'row' | 'stack' | 'bare' {
  if (typeof window === 'undefined') return 'row'
  if (window.innerWidth < 700 || window.innerHeight < 620) return 'bare'
  if (!aside) return 'row'
  return window.innerWidth >= ROW_MIN_W ? 'row' : 'stack'
}

/* 기기 위에 얹히는 상태 램프가 차지하는 세로. 이만큼 빼고 기기를 맞춰야
   램프가 화면 위로 잘리지 않는다 (막대 높이 + column 의 gap). */
const LAMP_H = 56

function fitScale(mode: 'row' | 'stack', aside: boolean) {
  const pad = 32
  const room =
    mode === 'row' && aside
      ? window.innerWidth - pad - ASIDE_RESERVE
      : window.innerWidth - pad
  // 폰이 알아볼 수 없을 만큼 줄어드는 것보다는 조금 좁아지는 편이 낫다.
  return Math.min(
    1,
    (window.innerHeight - pad - LAMP_H) / DEVICE_H,
    Math.max(room, DEVICE_W * 0.62) / DEVICE_W,
  )
}

export function DeviceFrame({
  children,
  aside,
}: {
  children: ReactNode
  /** 목업 바깥(오른쪽)에 세울 것. 시연 패널이 여기로 들어온다. */
  aside?: ReactNode
}) {
  const hasAside = aside !== undefined
  const [mode, setMode] = useState(() => layoutFor(hasAside))
  const [scale, setScale] = useState(1)

  useEffect(() => {
    const onResize = () => {
      const next = layoutFor(hasAside)
      setMode(next)
      if (next !== 'bare') setScale(fitScale(next, hasAside))
    }
    onResize()
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [hasAside])

  // 목업이 붙는 동안에는 페이지가 스크롤되면 안 된다 — 스크롤은 화면 안에서만.
  // 세로로 쌓였을 때는 예외다. 그때는 패널이 접힌 아래에 있어서
  // 페이지가 스크롤되지 않으면 아예 닿을 수가 없다.
  useEffect(() => {
    const lock = mode === 'row'
    document.body.classList.toggle('framed', lock)
    return () => document.body.classList.remove('framed')
  }, [mode])

  if (mode === 'bare') {
    return (
      <div className={s.bare}>
        <StatusLamp />
        {children}
        {aside}
      </div>
    )
  }

  return (
    <div className={s.stage} data-mode={mode}>
      {/* 기기와 램프를 한 기둥으로 묶는다. scale 이 device 에만 걸리므로
          이 기둥의 폭은 축소된 기기 폭에 맞춰 따로 준다 — 안 그러면
          램프만 원래 크기로 남아 기기보다 넓어진다. */}
      <div className={s.column} style={{ width: DEVICE_W * scale }}>
        <StatusLamp />
        {/* 축소된 실제 크기를 차지하는 상자.
            transform:scale 은 **레이아웃 크기를 바꾸지 않는다** — 기기는
            작아 보여도 자리는 원래 크기만큼 차지한다. 예전에는 그 남는
            자리를 음수 marginRight 로 상쇄했는데, 램프가 생기면서 세로
            남는 자리까지 문제가 됐다. 상자에 축소된 크기를 직접 주고
            안쪽을 top-left 기준으로 줄이면 가로·세로가 한 번에 맞는다. */}
        <div
          className={s.deviceBox}
          style={{ width: DEVICE_W * scale, height: DEVICE_H * scale }}
        >
          <div
            className={s.device}
            style={{
              width: DEVICE_W,
              height: DEVICE_H,
              transform: `scale(${scale})`,
              transformOrigin: 'top left',
            }}
          >
            <div
              className={s.screen}
              style={{
                left: SCREEN_X,
                top: SCREEN_Y,
                width: SCREEN_W,
                height: SCREEN_H,
              }}
            >
              {children}
            </div>
            {/* 베젤은 앱 위에 얹힌다. 구멍이 투명이라 앱이 그대로 비쳐 보이고,
                pointer-events:none 이라 클릭은 전부 앱으로 지나간다. */}
            <img className={s.bezel} src={bezel} alt="" aria-hidden />
          </div>
        </div>
      </div>
      {aside}
    </div>
  )
}
