import { useMemo, useState } from 'react'
import type { EquityPoint } from '../api/types'
import { krw, spanLabel } from '../lib/format'
import s from './EquityChart.module.css'

/**
 * 자산 추이 (Figma node 1:631/1:632 — 면 + 선).
 *
 * ⚠️ 점을 만들어내지 않는다. 서버가 실제로 관측한 시점만 그린다.
 * 서버를 방금 켰으면 점이 두어 개뿐이고, 화면도 그렇게 보여야 한다.
 * 없는 3개월치를 그럴듯하게 채우는 순간 이 화면은 거짓말이 된다.
 *
 * 색은 구간 손익의 방향을 따른다 — 오르면 --up, 내리면 --down.
 */
const W = 300
const H = 120
const PAD = 4

export function EquityChart({
  points,
  krwRate,
  className,
}: {
  points: EquityPoint[]
  krwRate: number
  className?: string
}) {
  const [hover, setHover] = useState<number | null>(null)

  const geo = useMemo(() => {
    if (points.length < 2) return null
    const xs = points.map((p) => p.ts)
    const ys = points.map((p) => p.total_micro)
    const x0 = Math.min(...xs)
    const x1 = Math.max(...xs)
    const y0 = Math.min(...ys)
    const y1 = Math.max(...ys)

    // 값이 전혀 안 변한 구간은 세로로 눌려 0으로 나뉜다. 가운데 선으로 눕힌다.
    const spanY = y1 - y0
    const px = (t: number) =>
      x1 === x0 ? W / 2 : PAD + ((t - x0) / (x1 - x0)) * (W - PAD * 2)
    const py = (v: number) =>
      spanY === 0 ? H / 2 : PAD + (1 - (v - y0) / spanY) * (H - PAD * 2)

    const coords = points.map((p) => [px(p.ts), py(p.total_micro)] as const)
    const line = coords.map(([x, y], i) => `${i ? 'L' : 'M'}${x} ${y}`).join(' ')
    const area = `${line} L${coords[coords.length - 1][0]} ${H} L${coords[0][0]} ${H} Z`
    const rising = ys[ys.length - 1] >= ys[0]
    return { coords, line, area, rising, x0, x1 }
  }, [points])

  if (!geo) {
    return (
      <div className={`${s.empty} ${className ?? ''}`}>
        <p>자산 추이를 그리려면 관측점이 2개 이상 필요합니다.</p>
        <p className={s.emptySub}>
          지금까지 {points.length}개 — 봇이 매매하거나 요약을 열 때마다 쌓입니다.
        </p>
      </div>
    )
  }

  const hovered = hover != null ? points[hover] : null

  return (
    <div className={`${s.wrap} ${className ?? ''}`}>
      <svg
        className={s.svg}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`자산 추이 ${points.length}개 관측점`}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          const rel = ((e.clientX - r.left) / r.width) * W
          let best = 0
          let bd = Infinity
          geo.coords.forEach(([x], i) => {
            const d = Math.abs(x - rel)
            if (d < bd) {
              bd = d
              best = i
            }
          })
          setHover(best)
        }}
      >
        <defs>
          <linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
            <stop
              offset="0%"
              stopColor={geo.rising ? 'var(--up)' : 'var(--down)'}
              stopOpacity="0.28"
            />
            <stop
              offset="100%"
              stopColor={geo.rising ? 'var(--up)' : 'var(--down)'}
              stopOpacity="0"
            />
          </linearGradient>
        </defs>

        <path d={geo.area} fill="url(#eqfill)" />
        <path
          d={geo.line}
          fill="none"
          stroke={geo.rising ? 'var(--up)' : 'var(--down)'}
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
        />

        {hover != null && (
          <line
            x1={geo.coords[hover][0]}
            x2={geo.coords[hover][0]}
            y1="0"
            y2={H}
            stroke="var(--track)"
            strokeWidth="1"
            vectorEffect="non-scaling-stroke"
          />
        )}
      </svg>

      {/* 점이 적을 때는 마커를 찍어 '몇 번 관측했는지'가 보이게 한다.
          점이 많으면 선만으로 충분하고, 마커가 오히려 선을 가린다. */}
      {points.length <= 12 && (
        <svg className={s.dots} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          {geo.coords.map(([x, y], i) => (
            <circle
              key={i}
              cx={x}
              cy={y}
              r={hover === i ? 3.4 : 2.2}
              fill="var(--surface)"
              stroke={geo.rising ? 'var(--up)' : 'var(--down)'}
              strokeWidth="1.6"
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>
      )}

      {hovered && (
        <div
          className={s.tip}
          style={{
            left: `${(geo.coords[hover!][0] / W) * 100}%`,
          }}
        >
          <b className="tnum">{krw((hovered.total_micro / 1_000_000) * krwRate)}</b>
          <span>
            {new Date(hovered.ts * 1000).toLocaleString('ko-KR', {
              month: '2-digit',
              day: '2-digit',
              hour: '2-digit',
              minute: '2-digit',
            })}
          </span>
        </div>
      )}

      <p className={s.span}>
        관측 {points.length}개 · {spanLabel(geo.x1 - geo.x0)}치
      </p>
    </div>
  )
}
