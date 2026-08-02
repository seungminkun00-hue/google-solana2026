import { useState } from 'react'
import type { Holding } from '../api/types'
import s from './Donut.module.css'

/**
 * 보유 비중 도넛 (Figma node 1:644~1:648).
 *
 * 조각 사이에 2px 바탕색 틈을 둔다 — 인접한 두 색이 닮았을 때 경계를
 * 만드는 건 색이 아니라 이 틈이다. 색만으로 구분하게 두지 않는다.
 * 이름·비율은 옆 범례에 직접 적히므로, 색을 못 읽어도 값은 읽힌다.
 */
const SIZE = 100
const R = 39
const STROKE = 22
const C = 2 * Math.PI * R
const GAP = 2 // px, 조각 사이 틈

export function Donut({
  holdings,
  centerTop = '보유',
  centerBottom = '비중',
}: {
  holdings: Holding[]
  centerTop?: string
  centerBottom?: string
}) {
  const [active, setActive] = useState<string | null>(null)
  const total = holdings.reduce((a, h) => a + h.value_micro, 0)

  if (!total) {
    return (
      <div className={s.wrap}>
        <div className={s.emptyRing} />
        <div className={s.center}>
          <span className={s.centerSub}>보유</span>
          <span className={s.centerSub}>없음</span>
        </div>
      </div>
    )
  }

  let offset = 0
  const arcs = holdings.map((h) => {
    const frac = h.value_micro / total
    const len = Math.max(C * frac - GAP, 1)
    const arc = {
      key: h.ticker,
      color: h.color,
      dash: `${len} ${C - len}`,
      // -90deg 에서 시작해 12시 방향부터 시계방향으로 돈다
      offset: -offset,
      frac,
    }
    offset += C * frac
    return arc
  })

  return (
    <div className={s.wrap}>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className={s.svg} role="img"
        aria-label="보유 종목 비중">
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={R}
          fill="none"
          stroke="var(--divider)"
          strokeWidth={STROKE}
        />
        {arcs.map((a) => (
          <circle
            key={a.key}
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke={a.color}
            strokeWidth={active && active !== a.key ? STROKE - 3 : STROKE}
            strokeDasharray={a.dash}
            strokeDashoffset={a.offset}
            opacity={active && active !== a.key ? 0.45 : 1}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
            onMouseEnter={() => setActive(a.key)}
            onMouseLeave={() => setActive(null)}
            className={s.arc}
          />
        ))}
      </svg>
      <div className={s.center}>
        <span>{centerTop}</span>
        <span>{centerBottom}</span>
      </div>
    </div>
  )
}
