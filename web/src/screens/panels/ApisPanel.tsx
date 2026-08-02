import { api, useApi } from '../../api/client'
import { ProviderMark } from '../../components/ProviderMark'
import { Plus } from '../../components/Icon'
import { krw, usdc } from '../../lib/format'
import s from './ApisPanel.module.css'

export function ApisPanel({ botId }: { botId: string }) {
  const { data, error, loading } = useApi(() => api.apis(botId), [botId], 10_000)

  if (loading) return <p className={s.state}>불러오는 중…</p>
  if (error) return <p className={`${s.state} ${s.err}`}>{error}</p>
  if (!data) return null

  const m = data.summary

  return (
    <div className={s.wrap}>
      {/* ── API 사용 요약 (Figma 1:920) ──────────────────── */}
      <section className={s.card}>
        <h3 className={s.cardTitle}>API 사용 요약</h3>

        <dl className={s.stats}>
          <div>
            <dt>총 호출</dt>
            <dd className="tnum">{m.calls.toLocaleString('ko-KR')}회</dd>
          </div>
          <div>
            <dt>총 사용금액</dt>
            <dd className="tnum">{krw(m.spend_krw)}</dd>
          </div>
          <div>
            <dt>일 평균 호출</dt>
            <dd className="tnum">{m.calls_per_day.toLocaleString('ko-KR')}회</dd>
          </div>
          <div>
            <dt>일 평균 비용</dt>
            <dd className="tnum">{krw(m.spend_per_day_krw)}</dd>
          </div>
        </dl>

        <p className={s.note}>
          관측 {m.span_days}일 · 총 {usdc(m.spend_micro, 6)} 지출 · 결제는 전부
          x402 페이월을 통과했습니다.
        </p>
      </section>

      {/* ── 연결된 API (Figma 1:895) ─────────────────────── */}
      <section className={s.card}>
        <header className={s.cardHead}>
          <h3 className={s.cardTitle}>연결된 API</h3>
          <span className={s.count}>{data.connected.length}개</span>
          <span className={s.addBtn}>
            <Plus size={20} />
            API 연결
          </span>
        </header>

        {data.connected.length === 0 ? (
          <p className={s.empty}>
            아직 이 봇이 결제한 API가 없습니다. 사이클을 돌리면 뉴스·스크리닝·
            심층추론·시세를 사면서 여기에 쌓입니다.
          </p>
        ) : (
          <ul className={s.rows}>
            {data.connected.map((c) => (
              <li key={c.key} className={s.row}>
                <ProviderMark providerKey={c.key} name={c.name} size={40} />

                <div className={s.rowMain}>
                  <p className={s.rowName}>{c.name}</p>
                  <div className={s.tags}>
                    {c.tags.map((t) => (
                      <span key={t} className={s.tag}>
                        {t}
                      </span>
                    ))}
                    {c.paysh && <span className={s.paysh}>Pay.sh</span>}
                  </div>
                </div>

                <div className={s.rowNum}>
                  <p className="tnum">{c.calls.toLocaleString('ko-KR')}회</p>
                  <p className={`${s.rowPct} tnum`}>({c.calls_pct}%)</p>
                </div>

                <div className={s.rowNum}>
                  <p className="tnum">{krw(c.spend_krw)}</p>
                  <p className={`${s.rowPct} tnum`}>({c.spend_pct}%)</p>
                </div>
              </li>
            ))}
          </ul>
        )}

        {data.connected.length > 0 && (
          <p className={s.priceNote}>
            단가(µUSDC) — 뉴스 {data.price_table.exa_search.toLocaleString()} ·
            스크리닝 {data.price_table.gemini_flash.toLocaleString()} · 심층추론{' '}
            {data.price_table.gemini_deep.toLocaleString()} · 시세{' '}
            {data.price_table.market_quote.toLocaleString()}
          </p>
        )}
      </section>

      {/* ── 최근 호출 — 호출 한 건 = 결제 한 건 = 서명 하나 ───────
          합계만 보여주면 '사용량 결제'라는 주장을 확인할 방법이 없다.
          건별로 펼쳐서 devnet 서명을 직접 짚어볼 수 있게 한다. */}
      {data.recent.length > 0 && (
        <section className={s.card}>
          <header className={s.cardHead}>
            <h3 className={s.cardTitle}>최근 결제 내역</h3>
            <span className={s.count}>{data.recent.length}건</span>
          </header>

          <ul className={s.calls}>
            {data.recent.map((c) => (
              <li key={`${c.ts}-${c.tx}`} className={s.call}>
                <div className={s.callMain}>
                  <span className={s.callName}>{c.name}</span>
                  {c.model && (
                    <span
                      className={c.model === 'mock' ? s.modelMock : s.model}
                      title="이 호출에 실제로 답한 추론 모델"
                    >
                      {c.model === 'mock' ? '모의 판단' : c.model}
                    </span>
                  )}
                </div>
                <span className={`${s.callAmt} tnum`}>
                  {c.amount_micro.toLocaleString()} µ
                </span>
                {c.explorer ? (
                  <a
                    className={s.callTx}
                    href={c.explorer}
                    target="_blank"
                    rel="noreferrer"
                    title={c.tx}
                  >
                    {c.tx.slice(0, 6)}…{c.tx.slice(-4)}
                  </a>
                ) : (
                  <span className={s.callNoTx}>—</span>
                )}
              </li>
            ))}
          </ul>

          <p className={s.note}>
            {data.ledger_mode === 'devnet'
              ? '서명을 누르면 Solana devnet Explorer 에서 같은 결제를 볼 수 있습니다.'
              : '모의 원장입니다 — 온체인에 대조할 트랜잭션이 없습니다.'}
          </p>
        </section>
      )}

      {/* ── 추천 API (Figma 1:871~1:873) ─────────────────── */}
      <section className={s.reco}>
        <header className={s.recoHead}>
          <h3 className={s.recoTitle}>추천 API</h3>
          <span className={s.allBtn}>
            <Plus size={20} />
            전체보기
          </span>
        </header>
        <p className={s.recoSub}>
          이 리포지토리에 어댑터가 이미 있는, 아직 켜지 않은 경로입니다.
        </p>

        <ul className={s.carousel}>
          {data.recommended.map((r) => (
            <li key={r.key} className={s.recoCard}>
              <div className={s.recoTop}>
                <ProviderMark providerKey={r.key} name={r.name} size={35} />
                <p className={s.recoName}>{r.name}</p>
              </div>
              <div className={s.tags}>
                {r.tags.map((t) => (
                  <span key={t} className={s.tag}>
                    {t}
                  </span>
                ))}
              </div>
              <p className={s.recoDesc}>{r.desc}</p>
              <p className={s.adapter}>{r.adapter}</p>
              <span
                className={`${s.connect} ${r.ready ? '' : s.connectOff}`}
                title={
                  r.ready
                    ? '환경변수 하나로 켜집니다'
                    : '아직 켤 수 없습니다 — 남은 작업은 설명 참조'
                }
              >
                {r.ready ? `연결하기 · ${r.price_note}` : '준비 중'}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
