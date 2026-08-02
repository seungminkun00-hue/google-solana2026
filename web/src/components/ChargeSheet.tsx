import { useEffect, useState } from 'react'
import { useApi } from '../api/client'
import { judgeApi, type JudgeStatus } from '../api/judge'
import type { BotCard } from '../api/types'
import { useActivity } from './activityLog'
import s from './ChargeSheet.module.css'

const USDC = 1_000_000

/**
 * 앱 안의 '충전하기'.
 *
 * [무엇이 실제로 일어나나]
 * 연결된 팬텀 지갑에서 고른 봇의 지갑으로 devnet USDC 가 **실제로** 이동한다
 * (`POST /judge/deposit`). 위임을 미리 받아뒀기 때문에 여기서 서명은 없다 —
 * 그게 이 제품의 주장이고, 충전 버튼이 그걸 가장 일상적인 모습으로 보여준다.
 *
 * 성공하면 화면 위 팝업이 뜬다. 그 알림은 서버가 내보내는 이벤트를 타고
 * 오므로(app/core/events.py), 이 시트가 직접 띄우지 않는다 — 매수 알림과
 * 같은 통로를 쓴다.
 *
 * [왜 지갑이 필요한가를 그대로 말한다]
 * 지갑을 안 붙였거나 위임이 없으면 충전할 돈의 출처가 없다. 버튼을
 * 비활성으로만 두면 왜 안 되는지 알 수 없으므로, 무엇을 먼저 해야 하는지
 * 그 자리에 적는다.
 */
export function ChargeSheet({
  bots,
  defaultBotId,
  onClose,
}: {
  bots: BotCard[]
  defaultBotId?: string
  onClose: () => void
}) {
  const [botId, setBotId] = useState(defaultBotId ?? bots[0]?.bot_id ?? '')
  const [amount, setAmount] = useState(50)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const activity = useActivity()

  // 지갑 상태는 서버가 온체인에서 확인해 준다. 잔고·남은 한도를 그대로 쓴다.
  const { data: judge, reload } = useApi<JudgeStatus>(
    () => judgeApi.status(),
    [],
    5_000,
  )

  useEffect(() => {
    if (!botId && bots.length) setBotId(bots[0].bot_id)
  }, [bots, botId])

  const registered = judge?.registered === true
  const allowance = judge?.allowance ?? 0
  const balance = judge?.balance ?? 0
  const canCharge = registered && allowance > 0 && balance > 0 && !!botId

  async function submit() {
    if (busy || !canCharge) return
    setBusy(true)
    setErr(null)
    setDone(null)
    try {
      const name = bots.find((b) => b.bot_id === botId)?.name ?? botId
      activity.begin('judge', `앱 · 충전 — ${name} $${amount}`)
      const r = await judgeApi.deposit(botId, Math.round(amount * USDC))
      activity.push('judge', [{
        step: 'pull-done',
        amount_usd: r.amount_usd,
        bot_treasury_usd: (r.bot_treasury / USDC).toFixed(2),
        allowance_left_usd: (r.allowance_left / USDC).toFixed(2),
        tx: r.tx,
        explorer: r.explorer,
      }])
      setDone(`$${r.amount_usd.toFixed(2)} 충전됐습니다. 남은 한도 $${(
        r.allowance_left / USDC
      ).toFixed(2)}`)
      reload()
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={s.backdrop} onClick={onClose} role="presentation">
      <div
        className={s.sheet}
        role="dialog"
        aria-label="충전하기"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className={s.title}>충전하기</h3>
        <p className={s.sub}>연결된 지갑에서 봇 지갑으로 옮깁니다.</p>

        {!registered && (
          <p className={s.warn}>
            지갑이 연결되지 않았습니다. 옆의 「심사위원 지갑 시연」 탭에서
            ① 팬텀 연결 → ② 테스트 USDC 받기 → ③ 위임 서명을 먼저 해주세요.
          </p>
        )}
        {registered && allowance <= 0 && (
          <p className={s.warn}>
            위임이 없습니다. 「심사위원 지갑 시연」 ③ 에서 한 번 서명하면
            이후 충전은 서명 없이 됩니다.
          </p>
        )}

        <label className={s.field}>
          <span>어느 봇에</span>
          <select
            value={botId}
            onChange={(e) => setBotId(e.target.value)}
            disabled={busy || bots.length === 0}
          >
            {bots.length === 0 && <option value="">봇을 먼저 만들어주세요</option>}
            {bots.map((b) => (
              <option key={b.bot_id} value={b.bot_id}>
                {b.name}
              </option>
            ))}
          </select>
        </label>

        <label className={s.field}>
          <span>금액 (USDC)</span>
          <input
            type="number"
            min={1}
            step={10}
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            disabled={busy}
            className="tnum"
          />
        </label>

        <div className={s.quick}>
          {[10, 50, 100].map((v) => (
            <button
              key={v}
              type="button"
              className={amount === v ? s.quickOn : undefined}
              onClick={() => setAmount(v)}
              disabled={busy}
            >
              ${v}
            </button>
          ))}
        </div>

        <dl className={s.kv}>
          <dt>내 지갑 잔고</dt>
          <dd className="tnum">${(balance / USDC).toFixed(2)}</dd>
          <dt>남은 위임 한도</dt>
          <dd className="tnum">${(allowance / USDC).toFixed(2)}</dd>
        </dl>

        {err && <p className={s.err}>{err}</p>}
        {done && <p className={s.ok}>{done}</p>}

        <p className={s.note}>
          위임 한도 안에서는 <b>추가 서명 없이</b> 이동합니다. 한도를 넘으면
          체인이 거부합니다.
        </p>

        <div className={s.actions}>
          <button type="button" className={s.cancel} onClick={onClose} disabled={busy}>
            닫기
          </button>
          <button
            type="button"
            className={s.confirm}
            onClick={submit}
            disabled={busy || !canCharge}
          >
            {busy ? '충전 중…' : '충전하기'}
          </button>
        </div>
      </div>
    </div>
  )
}
