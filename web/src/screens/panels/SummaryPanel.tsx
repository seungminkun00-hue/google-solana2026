import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useApi } from '../../api/client'
import type { RunResult, SellResult } from '../../api/types'
import { Donut } from '../../components/Donut'
import { EquityChart } from '../../components/EquityChart'
import { useActivity } from '../../components/activityLog'
import { useTour } from '../../components/tour'
import {
  ArrowDown, ArrowUp, ChatBubble, Refresh, SparkleMark, StopIcon,
} from '../../components/Icon'
import { RANGE_LABELS, ago, krw, krwSigned, pctSigned, tone } from '../../lib/format'
import s from './SummaryPanel.module.css'

/**
 * 리포트 문장 안의 '매수'·'매도'만 색을 입힌다.
 *
 * 한국 시장 관행대로 매수는 빨강, 매도는 파랑이다(미국과 반대라
 * --up/--down 을 그대로 쓰면 안 된다 — 그건 손익용 색이다).
 * 글자만 칠하고 배경이나 굵기는 건드리지 않는다.
 */
function TradeWords({ text }: { text: string }) {
  return (
    <>
      {text.split(/(매수|매도)/).map((part, i) =>
        part === '매수' ? (
          <b key={i} className={s.buyWord}>
            매수
          </b>
        ) : part === '매도' ? (
          <b key={i} className={s.sellWord}>
            매도
          </b>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  )
}

/** 사이클 로그의 단계 이름을 사람 말로. 모르는 단계는 원문 그대로 둔다. */
const STEP_LABEL: Record<string, string> = {
  'budget-check': '예산 점검',
  scout: '뉴스 구매 + 1차 스크리닝',
  analyst: '시그널 구매 + 심층 추론',
  'external-sale': '외부 에이전트에 판단 판매',
  replenish: '인지비용 보충 (만다트 심사)',
  rulebook: '룰북 게이트',
  'capital-invoice': '매매자금 청구 (만다트 심사)',
  executor: '체결 — USDC 지불, 주식 토큰 수령',
  'position-open': '포지션 보유 시작',
}

export function SummaryPanel({ botId }: { botId: string }) {
  const [range, setRange] = useState('3m')
  const [busy, setBusy] = useState(false)
  const [run, setRun] = useState<RunResult | null>(null)
  const [running, setRunning] = useState(false)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [selling, setSelling] = useState(false)
  const [sold, setSold] = useState<SellResult | null>(null)
  const { markRan } = useTour()
  const activity = useActivity()

  // 10초 폴링. 스케줄러가 도는 동안 자산·포지션이 실제로 바뀐다.
  const { data, error, loading, reload } = useApi(
    () => api.summary(botId, range),
    [botId, range],
    10_000,
  )

  async function togglePause() {
    if (!data || busy) return
    setBusy(true)
    try {
      await api.pause(botId, !data.killed)
      reload()
    } catch (e) {
      alert(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  /** 봇을 한 사이클 돌린다. 실제 결제·추론·체결이 일어나므로 20초쯤 걸린다.
   *
   *  결과는 이 카드에도 남기고 오른쪽 공용 로그로도 보낸다. 카드는 방금
   *  누른 사람에게, 로그는 계속 지켜보는 사람에게 필요하다. */
  async function runNow() {
    if (running) return
    setRunning(true)
    setRunErr(null)
    setRun(null)
    activity.setBusy(true)
    activity.begin('cycle', `앱 · 지금 일해보기 — ${data?.name ?? botId}`)
    try {
      const r = await api.run(botId)
      setRun(r)
      activity.push('cycle', r.preflight)
      activity.push('cycle', r.log)
      activity.push('cycle', [{
        step: r.filled ? 'done' : 'blocked',
        filled: r.filled, attempts: r.attempts,
      }])
      markRan()
      reload()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setRunErr(msg)
      activity.push('cycle', [{ step: 'error', reason: msg }])
    } finally {
      setRunning(false)
      activity.setBusy(false)
    }
  }

  /** 전량 매도·정산. 청산 → 손익 확정 → 85/10/5 분배가 실제로 일어난다. */
  async function sellNow() {
    if (selling) return
    setSelling(true)
    setRunErr(null)
    setSold(null)
    activity.setBusy(true)
    activity.begin('cycle', `앱 · 전량 매도·정산 — ${data?.name ?? botId}`)
    try {
      const r = await api.sell(botId, true)
      setSold(r)
      activity.push(
        'cycle',
        r.sold.map((x) => ({
          step: 'settled',
          ticker: x.ticker,
          qty: x.qty,
          entry: x.entry_price,
          exit: x.exit_price,
          realized_pnl: x.realized_micro,
          distribution: x.distribution,
          tx: x.tx,
          explorer: x.explorer,
        })),
      )
      activity.push('cycle', [{
        step: 'done', closed: r.closed, realized_pnl_total: r.realized_micro,
      }])
      reload()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      setRunErr(msg)
      activity.push('cycle', [{ step: 'error', reason: msg }])
    } finally {
      setSelling(false)
      activity.setBusy(false)
    }
  }

  if (loading) return <p className={s.state}>불러오는 중…</p>
  if (error) return <p className={`${s.state} ${s.err}`}>{error}</p>
  if (!data) return null

  const p = data.performance
  const rate = data.fx.rate
  const headline = data.equity.window_return_pct ?? p.total_return_pct
  const headlineTone = tone(headline)
  // 곡선이 그 구간을 못 덮으면 헤드라인 숫자의 출처가 곡선이 아니라
  // 투입원가 기준 총수익률이다. 무엇을 보고 있는지 라벨로 밝힌다.
  const headlineIsWindow = data.equity.window_return_pct != null

  return (
    <div className={s.wrap}>
      {/* ── 지금 일해보기 ─────────────────────────────────
          시연에서 가장 중요한 버튼이라 맨 위에 둔다. 누르면 뉴스 구매부터
          체결까지 실제로 일어나고, 단계 로그를 그대로 보여준다. */}
      <section className={`${s.card} ${s.runCard}`}>
        <div className={s.runHead}>
          <div>
            <h3 className={s.cardTitle}>지금 일해보기</h3>
            <p className={s.runSub}>
              뉴스 구매 → 스크리닝 → 심층 추론 → 룰북 → 체결까지 한 번 돌립니다.
              <br />
              {/* 지갑 시연의 [매수 실행] 과 무엇이 다른지 여기서 밝힌다.
                  둘 다 같은 사이클을 돌리지만 자금 출처가 다르다. */}
              <em className={s.runDiff}>
                지갑을 연결하고 위임했다면 <b>심사위원 지갑에서 먼저 인출</b>한 뒤
                돌립니다(추가 서명 없음). 위임이 없으면 봇 지갑 안에서 돕니다.
              </em>
            </p>
          </div>
          <div className={s.runBtns}>
            <button
              type="button"
              className={s.runBtn}
              onClick={runNow}
              disabled={running || selling}
            >
              {running ? '일하는 중…' : '매수 사이클'}
            </button>
            {/* 매도가 없으면 한 바퀴가 안 닫힌다. 정산까지 봐야
                '자동매매'가 무엇을 하는 물건인지 전달된다. */}
            <button
              type="button"
              className={s.sellBtn}
              onClick={sellNow}
              disabled={running || selling || p.market_micro <= 0}
            >
              {selling ? '정산 중…' : '전량 매도·정산'}
            </button>
          </div>
        </div>

        {running && (
          <p className={s.runNote}>
            devnet 트랜잭션 확정을 기다립니다. 보통 15~30초 걸립니다.
          </p>
        )}
        {runErr && <p className={s.runErr}>{runErr}</p>}

        {sold && (
          <>
            <p className={sold.closed > 0 ? s.runOk : s.runNo}>
              {sold.closed > 0
                ? `${sold.closed}건 청산 · 실현손익 ${(sold.realized_micro / 1e6).toFixed(2)} USDC (${sold.realized_krw.toLocaleString('ko-KR')}원)`
                : (sold.note ?? '청산할 포지션이 없습니다.')}
            </p>
            <ol className={s.runLog}>
              {sold.sold.map((x) => (
                <li key={x.tx}>
                  <span className={s.runStep}>{x.flag} {x.company}</span>
                  <span className={s.runDetail}>
                    {x.qty}주 · ${x.entry_price.toFixed(2)} → ${x.exit_price.toFixed(2)}
                    {' · '}
                    {(x.realized_micro / 1e6).toFixed(2)} USDC
                    {x.distribution?.length
                      ? ` · 분배 ${x.distribution.length}건`
                      : ' · 손실이라 분배 없음'}
                  </span>
                </li>
              ))}
            </ol>
          </>
        )}

        {run && (
          <>
            <p className={run.filled ? s.runOk : s.runNo}>
              {run.filled
                ? `체결됐습니다 (시도 ${run.attempts}회). 화면 위 알림과 거래 내역 탭에서 확인하세요.`
                : '이번에는 체결까지 가지 않았습니다 — 아래 단계에서 어디서 멈췄는지 보입니다.'}
            </p>
            <ol className={s.runLog}>
              {run.log.map((st, i) => {
                const key = String(st.step ?? '?')
                const blocked = st.blocked ?? st.reason
                return (
                  <li key={i} data-bad={blocked ? 'true' : undefined}>
                    <span className={s.runStep}>{STEP_LABEL[key] ?? key}</span>
                    <span className={s.runDetail}>{detailOf(st)}</span>
                  </li>
                )
              })}
            </ol>
          </>
        )}
      </section>

      {/* ── AI 요약 리포트 (Figma 1:600) ─────────────────── */}
      <section className={s.card}>
        <header className={s.cardHead}>
          <SparkleMark size={14} className={s.spark} />
          <h3 className={s.cardTitle}>AI 요약 리포트</h3>
          <span className={s.ago}>
            {data.report.generated_at ? ago(data.report.generated_at) : '없음'}
          </span>
          <button
            type="button"
            className={s.refresh}
            onClick={reload}
            aria-label="새로고침"
          >
            <Refresh size={21} />
          </button>
        </header>

        <p className={s.report}>
          {/* 어느 나라 주식을 판단한 것인지. 환전 없이 전 세계를 산다는
              것이 이 제품의 주장이라, 종목명만으로는 그게 안 읽힌다. */}
          {data.report.flag && (
            <span className={s.reportFlag} aria-hidden>
              {data.report.flag}{' '}
            </span>
          )}
          <TradeWords text={data.report.text} />
        </p>

        {!data.report.empty && (
          <p className={s.provenance}>
            추론 {data.report.inference_mode}
            {data.report.degraded && (
              <span className={s.degraded}> · 폴백 발생(판매 불가 영수증)</span>
            )}
            {Object.entries(data.report.sources).map(([k, v]) => (
              <span key={k}>
                {' '}
                · {k}={v}
              </span>
            ))}
          </p>
        )}
      </section>

      {/* ── 수익률 + 자산 (Figma 1:603) ──────────────────── */}
      <section className={`${s.card} ${s.split}`}>
        <div className={s.left}>
          <h3 className={s.cardTitle}>
            {RANGE_LABELS[range]} 수익률
          </h3>

          <p className={`${s.bigPct} ${s[headlineTone]}`}>
            {headline == null ? (
              <span className={s.noData}>집계 전</span>
            ) : (
              <>
                {headlineTone === 'down' ? (
                  <ArrowDown size={24} />
                ) : (
                  <ArrowUp size={24} />
                )}
                <span className="tnum">
                  {Math.abs(headline).toFixed(2)}%
                </span>
              </>
            )}
          </p>

          <p className={s.headlineNote}>
            {headlineIsWindow ? '구간 자산 변화' : '투입원가 대비 총수익률'}
          </p>

          <EquityChart points={data.equity.points} krwRate={rate} />

          {!data.equity.covers_range && data.equity.points.length >= 2 && (
            <p className={s.accruing}>
              {RANGE_LABELS[range]}치가 아직 안 쌓였습니다 — 있는 구간만 그립니다.
            </p>
          )}

          <div className={s.ranges} role="tablist">
            {data.ranges.map((r) => (
              <button
                key={r}
                type="button"
                role="tab"
                aria-selected={r === range}
                className={`${s.range} ${r === range ? s.rangeOn : ''}`}
                onClick={() => setRange(r)}
              >
                {RANGE_LABELS[r] ?? r}
              </button>
            ))}
          </div>
        </div>

        <div className={s.right}>
          <h3 className={s.cardTitle}>현재 자산</h3>
          <p className={`${s.asset} tnum`}>{krw(p.current_krw)}</p>

          <dl className={s.subStats}>
            <div>
              <dt>투자 원금</dt>
              <dd className="tnum">{krw(p.basis_krw)}</dd>
            </div>
            <div>
              <dt>평가 손익</dt>
              <dd className={`${s[tone(p.unrealized_micro)]} tnum`}>
                {krwSigned(p.unrealized_krw)}
                <span className={s.subPct}>
                  ({pctSigned(p.unrealized_pct)})
                </span>
              </dd>
            </div>
            <div>
              <dt>실현 손익</dt>
              <dd className={`${s[tone(p.realized_micro)]} tnum`}>
                {krwSigned((p.realized_micro / 1_000_000) * rate)}
              </dd>
            </div>
            <div>
              <dt>현금 / 평가</dt>
              <dd className="tnum">
                {krw((p.cash_micro / 1_000_000) * rate)}
              </dd>
            </div>
          </dl>
        </div>
      </section>

      {/* ── 보유 주식 Top 5 (Figma 1:604) ────────────────── */}
      <section className={s.card}>
        <h3 className={s.cardTitle}>보유 주식 Top 5</h3>

        {data.holdings.length === 0 ? (
          <p className={s.emptyHold}>
            열린 포지션이 없습니다. 봇이 매수하면 여기에 비중이 그려집니다.
          </p>
        ) : (
          <div className={s.holdRow}>
            <Donut holdings={data.holdings} />
            <ul className={s.legend}>
              {data.holdings.map((h) => (
                <li key={h.ticker}>
                  <span
                    className={s.swatch}
                    style={{ background: h.color }}
                    aria-hidden
                  />
                  <span className={s.legendName} title={h.company}>
                    {h.flag} {h.company}
                  </span>
                  <span className="tnum">{h.weight_pct.toFixed(1)}%</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {/* ── 액션 (Figma 1:601 / 1:602) ───────────────────── */}
      <div className={s.actions}>
        <button
          type="button"
          className={s.pause}
          onClick={togglePause}
          disabled={busy}
        >
          <StopIcon size={24} />
          <span>{data.killed ? '재개하기' : '일시 정지'}</span>
        </button>
        <Link to={`/bot/${botId}/chat`} className={s.chat}>
          <ChatBubble size={31} />
          <span>대화하기</span>
        </Link>
      </div>
    </div>
  )
}

/** 사이클 로그 한 줄에서 사람이 볼 만한 부분만 뽑는다.
 *  단계마다 필드가 달라서 화이트리스트로 고르면 새 필드가 화면에서 사라진다 —
 *  그래서 '무엇이 있으면 그걸 쓴다' 순서로 본다. */
function detailOf(st: Record<string, unknown>): string {
  const s2 = (k: string) => (st[k] === undefined ? null : String(st[k]))
  const blocked = s2('blocked')
  if (blocked) return blocked
  if (st.step === 'scout') {
    return `${s2('verdict') ?? ''} · ${s2('headline') ?? ''}`.trim()
  }
  if (st.step === 'analyst') {
    return `${s2('ticker')} ${st.side === 'buy' ? '매수' : '매도'} · 확신도 ${s2('confidence')}`
  }
  if (st.step === 'executor' || st.step === 'position-open') {
    return `${s2('ticker')} ${s2('qty')}주 @ $${s2('entry_price')}`
  }
  if (st.decided_reason) return String(st.decided_reason)
  if (st.amount !== undefined) return `${s2('amount')} µUSDC`
  if (st.passed) return '통과'
  return ''
}
