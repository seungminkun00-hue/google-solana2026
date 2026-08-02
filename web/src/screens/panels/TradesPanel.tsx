import { api, useApi } from '../../api/client'
import { dateLines, hours, krwBare, pct, qty } from '../../lib/format'
import s from './TradesPanel.module.css'

export function TradesPanel({ botId }: { botId: string }) {
  const { data, error, loading } = useApi(
    () => api.trades(botId, 100),
    [botId],
    10_000,
  )

  if (loading) return <p className={s.state}>불러오는 중…</p>
  if (error) return <p className={`${s.state} ${s.err}`}>{error}</p>
  if (!data) return null

  const m = data.summary

  return (
    <div className={s.wrap}>
      {/* ── 거래 요약 (Figma 1:777) ──────────────────────── */}
      <section className={s.card}>
        <header className={s.cardHead}>
          <h3 className={s.cardTitle}>거래 요약</h3>
          <span className={s.sub}>(전체 기간)</span>
        </header>

        <dl className={s.stats}>
          <div>
            <dt>총 거래</dt>
            <dd className="tnum">{m.total_fills}회</dd>
          </div>
          <div>
            {/* 목업에는 '2.3회'로 적혀 있었지만 보유기간의 단위는 시간이다.
                서버는 매도 체결에 기록된 실제 보유 시간의 평균을 준다. */}
            <dt>평균 보유기간</dt>
            <dd className="tnum">{hours(m.avg_hold_hours)}</dd>
          </div>
          <div>
            <dt>체결 성공률</dt>
            <dd className="tnum">{pct(m.fill_rate_pct, 1)}</dd>
          </div>
          <div>
            <dt>승률</dt>
            <dd className="tnum">{pct(m.win_rate_pct, 1)}</dd>
          </div>
        </dl>

        <p className={s.note}>
          결정 {m.decisions}건 중 {m.settled}건 정산 · 매수 {m.buys} / 매도{' '}
          {m.sells} · 열린 포지션 {m.open_positions}개
          {data.ledger_mode === 'devnet' && (
            <>
              {' '}
              · 각 체결의 서명을 누르면 Solana devnet Explorer 에서 같은 수량을
              확인할 수 있습니다.
            </>
          )}
        </p>
      </section>

      {/* ── 거래 내역 (Figma 1:778) ──────────────────────── */}
      <section className={s.card}>
        <h3 className={s.cardTitle}>거래 내역</h3>

        {data.rows.length === 0 ? (
          <p className={s.empty}>
            아직 체결된 거래가 없습니다. 봇이 한 사이클을 완주하면 여기에
            매수부터 쌓입니다.
          </p>
        ) : (
          <div className={s.tableWrap}>
            <table className={s.table}>
              <thead>
                <tr>
                  {/* 폭이 좁아 '날짜/시간' 을 다 못 넣는다. 셀에 날짜와
                      시각이 두 줄로 들어가므로 머리글은 '날짜' 로 줄인다. */}
                  <th>날짜</th>
                  <th>종목명</th>
                  <th>거래</th>
                  <th className={s.num}>수량</th>
                  <th className={s.num}>단가(원)</th>
                  <th className={s.num}>총금액(원)</th>
                  {/* 이 체결이 온체인에 실제로 있다는 증거 */}
                  <th className={s.num}>서명</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => {
                  const [d, t] = dateLines(r.ts)
                  return (
                    <tr key={r.fill_id}>
                      <td className={s.when}>
                        <span>{d}</span>
                        <span>{t}</span>
                      </td>
                      <td className={s.sym}>
                        <span className={s.symName} title={r.company}>
                          {r.flag} {r.company}
                        </span>
                        <span className={s.symCode}>{r.ticker}</span>
                      </td>
                      <td className={r.side === 'buy' ? s.buy : s.sell}>
                        {r.side_ko}
                      </td>
                      <td className={`${s.num} tnum`}>{qty(r.qty)}</td>
                      <td className={`${s.num} tnum`}>
                        {krwBare(r.price_krw)}
                      </td>
                      <td className={`${s.num} tnum`}>
                        {krwBare(r.gross_krw)}
                        {r.pnl_micro != null && (
                          <span
                            className={
                              r.pnl_micro >= 0 ? s.pnlUp : s.pnlDown
                            }
                          >
                            {r.pnl_micro >= 0 ? '+' : ''}
                            {(r.pnl_micro / 1_000_000).toFixed(2)} USDC
                          </span>
                        )}
                      </td>
                      <td className={`${s.num} ${s.tx}`}>
                        {r.explorer ? (
                          <a href={r.explorer} target="_blank" rel="noreferrer" title={r.tx}>
                            {r.tx.slice(0, 4)}…{r.tx.slice(-4)}
                          </a>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
