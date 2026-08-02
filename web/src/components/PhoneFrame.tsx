import type { ReactNode } from 'react'
import s from './PhoneFrame.module.css'

/**
 * 디자인 폭 402px 을 그대로 쓰는 셸.
 *
 * 데스크톱에서는 가운데 고정 폭으로 세우고, 폭이 402 미만인 실제 폰에서는
 * 화면을 꽉 채운다. 안쪽 레이아웃은 전부 상대 단위라 두 경우 모두
 * 같은 비율로 보인다.
 *
 * scroll 영역과 하단 탭을 분리한 이유: 탭이 스크롤을 따라 움직이면
 * 목록이 길어질 때 하단이 가려진다. 탭 높이만큼 아래 여백을 준다.
 */
export function PhoneFrame({
  children,
  nav,
  className,
  /** 본문을 세로 flex 로 만든다. 채팅처럼 입력창이 바닥에 붙어야
      하는 화면에서 쓴다 (margin-top:auto 가 먹으려면 부모가 flex 여야 한다). */
  column,
  /** 하단 탭이 없는 화면은 아래 여백이 필요 없다. */
  flush,
}: {
  children: ReactNode
  nav?: ReactNode
  className?: string
  column?: boolean
  flush?: boolean
}) {
  const scrollCls = [s.scroll, column ? s.column : '', flush ? s.flush : '']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={s.shell}>
      <div className={`${s.screen} ${className ?? ''}`}>
        <div className={scrollCls}>{children}</div>
        {/* 상태바·다이나믹 아일랜드 자리를 덮는 판. 디자인에도 프레임마다
            같은 것이 있다 (Rectangle 4197, 상단 126px 배경).
            이게 없으면 스크롤한 내용이 시계·배터리 위로 올라온다.
            모든 화면의 헤더가 74px 아래에서 시작하므로 가리지 않는다. */}
        <div className={s.statusScrim} aria-hidden />
        {nav}
      </div>
    </div>
  )
}
