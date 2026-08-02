import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { AppEvent, DepositEvent, FillEvent } from '../api/types'
import { krw, qty as fmtQty } from '../lib/format'
import s from './FillToasts.module.css'

/**
 * 매수·매도가 체결되면 화면 위에서 내려오는 알림.
 *
 * [왜 폴링인가]
 * 체결은 사용자가 보고 있지 않을 때도 일어난다(스케줄러·다른 탭에서 돌린
 * 사이클). SSE 를 하나 더 열 수도 있지만, 이 앱의 다른 조회가 전부 폴링이라
 * 연결 관리 방식을 둘로 나눌 이유가 없다. 3초면 사람이 '즉시'로 느낀다.
 *
 * [왜 seq 인가]
 * 시각으로 비교하면 같은 초에 두 건이 들어올 때 하나를 놓친다. 서버가
 * 붙여주는 단조 증가 번호만 기억하고 그 뒤엣것만 받는다.
 *
 * 첫 요청은 since 없이 보낸다 — 지금 위치만 받아오고 과거 체결은 띄우지
 * 않는다. 화면을 열자마자 지난 알림이 쏟아지면 그건 알림이 아니라 소음이다.
 */
const POLL_MS = 3_000
const SHOW_MS = 8_000

export function FillToasts() {
  const [items, setItems] = useState<AppEvent[]>([])
  const seq = useRef<number | null>(null)
  const timers = useRef<number[]>([])

  useEffect(() => {
    let alive = true

    const tick = async () => {
      try {
        const r = await api.events(seq.current ?? undefined)
        if (!alive) return
        // 첫 응답은 위치만 잡는다. events 는 항상 비어 있다.
        if (seq.current === null) {
          seq.current = r.seq
          return
        }
        seq.current = r.seq
        if (r.events.length === 0) return

        setItems((prev) => [...prev, ...r.events].slice(-3))
        for (const e of r.events) {
          timers.current.push(
            window.setTimeout(
              () => setItems((prev) => prev.filter((x) => x.seq !== e.seq)),
              SHOW_MS,
            ),
          )
        }
      } catch {
        // 알림을 못 받은 것뿐이다. 화면에 에러를 띄울 일은 아니다.
      }
    }

    tick()
    const t = setInterval(tick, POLL_MS)
    return () => {
      alive = false
      clearInterval(t)
      timers.current.forEach(clearTimeout)
      timers.current = []
    }
  }, [])

  if (items.length === 0) return null

  return (
    <div className={s.stack} role="status" aria-live="polite">
      {items.map((e) => (
        <Toast
          key={e.seq}
          e={e}
          onClose={() => setItems((prev) => prev.filter((x) => x.seq !== e.seq))}
        />
      ))}
    </div>
  )
}

function Toast({ e, onClose }: { e: AppEvent; onClose: () => void }) {
  if (e.kind === 'deposit') return <DepositToast e={e} onClose={onClose} />
  return <FillToast e={e} onClose={onClose} />
}

/** 충전 — 지갑에서 봇으로 돈이 들어온 순간. */
function DepositToast({ e, onClose }: { e: DepositEvent; onClose: () => void }) {
  return (
    <div className={`${s.toast} ${s.deposit}`}>
      <div className={s.head}>
        <span className={s.tagDeposit}>충전 완료</span>
        <span className={s.bot}>{e.bot_name}</span>
        <button type="button" className={s.close} onClick={onClose} aria-label="닫기">
          ×
        </button>
      </div>
      <p className={`${s.line} ${s.nums} tnum`}>
        {e.source} → {krw(e.amount_krw)} ({(e.amount_micro / 1_000_000).toFixed(2)} USDC)
      </p>
      {e.explorer && (
        <a className={s.tx} href={e.explorer} target="_blank" rel="noreferrer">
          devnet 트랜잭션 보기
        </a>
      )}
    </div>
  )
}

function FillToast({ e, onClose }: { e: FillEvent; onClose: () => void }) {
  const buy = e.side === 'buy'
  const pnl = e.pnl_micro == null ? null : e.pnl_micro / 1_000_000

  return (
    <div className={`${s.toast} ${buy ? s.buy : s.sell}`}>
      <div className={s.head}>
        <span className={s.tag}>{buy ? '매수 체결' : '매도 체결'}</span>
        <span className={s.bot}>{e.bot_name}</span>
        <button type="button" className={s.close} onClick={onClose} aria-label="닫기">
          ×
        </button>
      </div>

      <p className={s.line}>
        <b>
          {e.flag} {e.company}
        </b>
        <span className={s.code}>{e.ticker}</span>
      </p>

      <p className={`${s.line} ${s.nums} tnum`}>
        {fmtQty(e.qty / 1_000_000)}주 · {krw(e.gross_krw)}
        {pnl != null && (
          <span className={pnl >= 0 ? s.up : s.down}>
            {pnl >= 0 ? ' +' : ' '}
            {pnl.toFixed(2)} USDC
          </span>
        )}
      </p>

      {e.reason && <p className={s.reason}>{e.reason}</p>}

      {/* 온체인에 실제로 있다는 증거. devnet 서명일 때만 붙는다. */}
      {e.explorer && (
        <a className={s.tx} href={e.explorer} target="_blank" rel="noreferrer">
          devnet 트랜잭션 보기
        </a>
      )}
    </div>
  )
}
