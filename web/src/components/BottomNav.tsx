import { NavFinance, NavGift, NavGoods, NavHome, NavStock } from './Icon'
import s from './BottomNav.module.css'

/**
 * 하단 탭 (Figma node 1:502).
 *
 * 이 앱에서 실제로 구현된 것은 '주식' 하나다. 나머지 넷은 디자인에
 * 있으므로 자리를 지키되, 눌러도 아무 데도 가지 않는다. 없는 화면으로
 * 보내 빈 페이지를 띄우는 것보다 낫고, 있는 척하는 것보다도 낫다.
 */
// w/h 는 Figma 그룹 `tap`(12:260)에서 잘라낸 각 아이콘의 실제 크기다.
// 정사각으로 뭉뚱그리면 mask가 늘어나 획이 굵어지거나 형태가 눌린다.
const TABS = [
  { key: 'home', label: '홈', Icon: NavHome, w: 21.5, h: 22.5, ready: false },
  { key: 'finance', label: '금융', Icon: NavFinance, w: 24, h: 24, ready: false },
  { key: 'goods', label: '상품', Icon: NavGoods, w: 24, h: 21.5, ready: false },
  { key: 'gift', label: '혜택', Icon: NavGift, w: 27, h: 22, ready: false },
  { key: 'stock', label: '주식', Icon: NavStock, w: 16, h: 16.5, ready: true },
] as const

export function BottomNav({ active = 'stock' }: { active?: string }) {
  return (
    <nav className={s.bar} aria-label="주요 메뉴">
      {TABS.map(({ key, label, Icon, w, h, ready }) => {
        const on = key === active
        return (
          <button
            key={key}
            type="button"
            className={`${s.tab} ${on ? s.on : ''}`}
            aria-current={on ? 'page' : undefined}
            aria-disabled={!ready}
            title={ready ? label : `${label} — 이 데모에는 없는 화면입니다`}
          >
            {/* 아이콘 높이를 24로 맞춰 다섯 개의 밑선이 나란히 놓이게 한다.
                가로는 원본 비율 그대로 — 늘리지 않는다. */}
            <span className={s.slot}>
              <Icon size={w} style={{ height: h }} className={s.icon} />
            </span>
            <span className={s.label}>{label}</span>
          </button>
        )
      })}
    </nav>
  )
}
