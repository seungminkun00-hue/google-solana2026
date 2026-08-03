import {
  useCallback, useEffect, useMemo, useRef, useState, type ReactNode,
} from 'react'
import {
  ActivityCtx, STEP_LABEL, useActivity, type LogEntry, type LogGroup,
} from './activityLog'
import s from './ActivityPanel.module.css'

const MAX_ENTRIES = 400

/** 로그 저장소. 목업과 옆 패널을 모두 감싸는 자리에 둔다. */
export function ActivityProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)

  const push = useCallback((group: LogGroup, rows: Record<string, unknown>[]) => {
    if (!rows.length) return
    setEntries((prev) => {
      const next = [...prev]
      for (const r of rows) {
        const { step, t, tx, explorer, ...rest } = r
        next.push({
          id: ++seq.current,
          group,
          step: String(step ?? '?'),
          t: typeof t === 'number' ? t : undefined,
          tx: typeof tx === 'string' ? tx : undefined,
          explorer: typeof explorer === 'string' ? explorer : undefined,
          rest: rest as Record<string, unknown>,
        })
      }
      return next.slice(-MAX_ENTRIES)
    })
  }, [])

  const begin = useCallback(
    (group: LogGroup, label: string) => {
      push(group, [{ step: '—', label }])
    },
    [push],
  )

  const clear = useCallback(() => setEntries([]), [])

  const value = useMemo(
    () => ({ entries, push, begin, clear, busy, setBusy }),
    [entries, push, begin, clear, busy],
  )
  return <ActivityCtx.Provider value={value}>{children}</ActivityCtx.Provider>
}

/**
 * 항상 오른쪽에 서 있는 실행 로그.
 *
 * 탭(진행 안내 / 지갑 시연)과 무관하게 계속 보인다 — 심사위원이 안내를
 * 읽는 동안에도 봇이 무엇을 하고 있는지 눈에서 사라지면 안 된다.
 */
export function ActivityPanel() {
  const { entries, clear, busy } = useActivity()
  const boxRef = useRef<HTMLDivElement>(null)
  const [big, setBig] = useState(false)

  // 새 줄이 붙으면 맨 아래로. 확대 전환 직후에도 다시 내린다.
  useEffect(() => {
    boxRef.current?.scrollTo({ top: boxRef.current.scrollHeight })
  }, [entries, big])

  useEffect(() => {
    if (!big) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setBig(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [big])

  const box = (
    <div className={s.log} ref={boxRef} data-big={big}>
      {entries.length === 0 ? (
        <p className={s.empty}>
          아직 실행된 것이 없습니다.
          <br />
          앱의 <b>[지금 일해보기]</b> 또는 지갑 시연의 <b>[매수 실행]</b> 을
          누르면 결제·추론·체결이 여기에 단계별로 쌓입니다.
        </p>
      ) : (
        entries.map((e) => <Row key={e.id} e={e} />)
      )}
    </div>
  )

  return (
    <section className={s.panel}>
      <header className={s.head}>
        <span className={s.title}>실행 로그</span>
        {busy && <span className={s.live}>실행 중</span>}
        <button
          type="button"
          className={s.btn}
          onClick={() => setBig(true)}
          disabled={entries.length === 0}
        >
          확대
        </button>
        <button
          type="button"
          className={s.btn}
          onClick={clear}
          disabled={entries.length === 0}
        >
          지우기
        </button>
      </header>

      {!big && box}
      {big && <p className={s.empty}>확대해서 보는 중입니다.</p>}

      {big && (
        <div className={s.overlay} onClick={() => setBig(false)} role="presentation">
          {/* 안쪽 클릭으로는 닫히지 않게 — 해시를 긁어 복사하다 닫히면 곤란하다 */}
          <div
            className={s.overlayBox}
            onClick={(ev) => ev.stopPropagation()}
            role="presentation"
          >
            <div className={s.head}>
              <span className={s.title}>실행 로그</span>
              <button type="button" className={s.btn} onClick={() => setBig(false)}>
                닫기
              </button>
            </div>
            {box}
          </div>
        </div>
      )}
    </section>
  )
}

function short(v: string): string {
  return v.length > 12 ? `${v.slice(0, 6)}…${v.slice(-4)}` : v
}

function Row({ e }: { e: LogEntry }) {
  if (e.step === '—') {
    return (
      <div className={s.divider}>
        <span>{String(e.rest.label ?? '')}</span>
      </div>
    )
  }

  const bad =
    e.step === 'error' ||
    e.step === 'blocked' ||
    e.step === 'stream-failed' ||
    e.step === 'chat-order-rejected' ||
    'blocked' in e.rest
  const label = STEP_LABEL[e.step] ?? e.step
  const pairs = Object.entries(e.rest)

  return (
    <div className={s.row} data-bad={bad} data-group={e.group}>
      <div className={s.rowHead}>
        <span className={s.step}>{label}</span>
        {e.t !== undefined && (
          <span className={`${s.time} tnum`}>{e.t.toFixed(1)}s</span>
        )}
      </div>
      {pairs.length > 0 && (
        <div className={s.body}>
          {pairs.map(([k, v]) => (
            <span key={k} className={s.tag}>
              {k}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </span>
          ))}
        </div>
      )}
      {e.tx && (
        <a
          className={s.tx}
          href={e.explorer ?? `https://explorer.solana.com/tx/${e.tx}?cluster=devnet`}
          target="_blank"
          rel="noreferrer"
        >
          Explorer에서 보기 · {short(e.tx)}
        </a>
      )}
    </div>
  )
}
