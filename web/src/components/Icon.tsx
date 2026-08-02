/**
 * Figma에서 뽑은 아이콘.
 *
 * SVG를 `<img>` 로 넣지 않고 CSS mask 로 칠하는 이유: 원본 SVG에는 색이
 * 박혀 있어서(#7E8CA4 등) 활성/비활성 상태를 바꿀 수 없다. mask 로 두면
 * 모양만 쓰고 색은 currentColor 가 정한다.
 *
 * 에셋 경로는 전부 Icon.module.css 에 있다 — React 19 가 인라인 style 안의
 * url(...) 을 버리기 때문이다(실측). 여기서는 위치·회전만 넘긴다.
 *
 * 벡터를 손으로 다시 그리지 않는다 — 전부 Figma 원본 바이트다.
 */
import type { CSSProperties, ReactNode } from 'react'
import s from './Icon.module.css'

type Box = { left: number; top: number; width: number; height: number }

type MaskProps = {
  /** Icon.module.css 의 에셋 클래스 */
  cls: string
  /** 부모 박스 기준 % 위치. Figma 좌표를 그대로 환산한 값이다. */
  box?: Box
  rotate?: number
  flipX?: boolean
}

function Mask({ cls, box, rotate, flipX }: MaskProps) {
  const t = [rotate ? `rotate(${rotate}deg)` : '', flipX ? 'scaleX(-1)' : '']
    .filter(Boolean)
    .join(' ')

  const style: CSSProperties = {
    ...(box
      ? {
          left: `${box.left}%`,
          top: `${box.top}%`,
          width: `${box.width}%`,
          height: `${box.height}%`,
        }
      : { inset: 0 }),
    ...(t ? { transform: t } : {}),
  }

  return <span className={`${s.mask} ${cls}`} style={style} />
}

export type IconProps = {
  size?: number
  className?: string
  style?: CSSProperties
}

function Frame({
  size = 24,
  className,
  style,
  children,
}: IconProps & { children: ReactNode }) {
  return (
    <span
      className={`${s.frame} ${className ?? ''}`}
      style={{ width: size, height: size, ...style }}
    >
      {children}
    </span>
  )
}

// ── 하단 탭 (Figma 그룹 `tap` 12:260 을 아이콘별 viewBox 로 자른 것) ──
export const NavHome = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.tabHome} />
  </Frame>
)

/** 금융 — 원 안의 W. W는 원본에서 흰색이라 mask로 칠하면 원에 묻힌다.
 *  바탕색으로 한 겹 더 덮어 다시 뚫는다. */
export const NavFinance = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.tabFinance} />
    <Mask cls={`${s.tabFinanceW} ${s.knockout}`} />
  </Frame>
)

export const NavGoods = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.tabGoods} />
  </Frame>
)

export const NavGift = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.tabGift} />
  </Frame>
)

export const NavStock = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.tabStock} />
  </Frame>
)

// ── 낱개 ─────────────────────────────────────────────────────────
export const ArrowUp = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.arrowUp} />
  </Frame>
)

/** 하락은 같은 화살표를 90° 돌려 쓴다. 별도 에셋이 없다. */
export const ArrowDown = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.arrowUp} rotate={90} />
  </Frame>
)

export const Plus = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.plus} />
  </Frame>
)

export const ProfileIcon = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.hdrProfile} />
  </Frame>
)

export const ChevronDown = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.chevronDown} />
  </Frame>
)

export const ChevronLeft = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.chevronDown} rotate={90} />
  </Frame>
)

export const Settings = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.settings} />
  </Frame>
)

/** 뒤로 — Figma 원본은 아래를 향한 갈매기(1:654)라 90° 돌려 쓴다. */
export const Back = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.back} box={{ left: 5, top: 20, width: 90, height: 58 }} rotate={90} />
  </Frame>
)

/** 새로고침 (Figma 1:659 Refresh_light). 원본 크기 21 */
export const Refresh = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actRefresh} />
  </Frame>
)

/** 대화하기 (Figma 1:655 Chat_alt_2_light). 원본 크기 31 */
export const ChatBubble = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actChat} />
  </Frame>
)

/** 일시 정지 (Figma 1:681 Stop_light). 원본 크기 24 */
export const StopIcon = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actStop} />
  </Frame>
)

/** AI 리포트 앞의 네 갈래 반짝임 (Figma 1:630) */
export const SparkleMark = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.sparkleMark} />
  </Frame>
)

// ── 설정 화면 (Figma 1:1039) ─────────────────────────────────────
// 팩토리로 묶으면 짧아지지만 Fast Refresh 가 컴포넌트로 못 알아본다.
// 하나씩 적어두는 편이 개발 중 새로고침 동작이 정상이다.
export const TagIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setTag} /></Frame>
)
export const CandleIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setCandle} /></Frame>
)
export const TargetIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setTarget} /></Frame>
)
export const SlidersIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setSliders} /></Frame>
)
export const WorldIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setWorld} /></Frame>
)
export const PipeIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setPipe} /></Frame>
)
export const TimeIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setTime} /></Frame>
)
export const MoneyIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setMoney} /></Frame>
)
export const CloseRound = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setClose} /></Frame>
)
export const AddRound = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setAdd} /></Frame>
)
export const QuestionIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setQuestion} /></Frame>
)
export const BellIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setBell} /></Frame>
)
export const ReinvestIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setReinvest} /></Frame>
)
export const CheckRing = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setCheck} /></Frame>
)
export const DateRangeIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setDaterange} /></Frame>
)
export const LightningIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setLightning} /></Frame>
)
export const CameraIcon = (p: IconProps) => (
  <Frame {...p}><Mask cls={s.setCamera} /></Frame>
)

/** 지갑 (Figma 1:555 Wallet_alt_light) — 원본 크기 24 */
export const Wallet = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actWallet} />
  </Frame>
)

/** 출금하기 — 종이비행기 (Figma 1:550 Send_hor_light).
 *  -45° 회전이 내보낸 파일에 이미 들어 있다. 원본 크기 49. */
export const Send = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actSend} />
  </Frame>
)

/** 충전하기 — 원 안의 + (Figma 1:554 Chat_alt_add_light). 원본 크기 35.
 *  이름은 Chat_alt_add 지만 실제로 그려진 것은 말풍선이 아니라
 *  오른쪽 아래만 각진 원이다. */
export const ChatAdd = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actCharge} />
  </Frame>
)

/** 전환하기 — 아래·위 화살표 (Figma 1:558 Collapse_light). 원본 크기 30 */
export const Swap = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.actSwap} />
  </Frame>
)

/** 검색 — 돋보기 (`onboarding icon right` 에서 잘라낸 13×13) */
export const Search = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.hdrSearch} />
  </Frame>
)

/** 정렬 — 길이가 다른 선 3개 위에 돋보기 (같은 그룹에서 잘라낸 20×18) */
export const Filter = (p: IconProps) => (
  <Frame {...p}>
    <Mask cls={s.hdrSort} />
  </Frame>
)
