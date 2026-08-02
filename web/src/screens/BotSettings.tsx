import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, useApi } from '../api/client'
import type { BotAi, DeletePreflight, Profile } from '../api/types'
import { krw, pctSigned, qty as fmtQty } from '../lib/format'
import { BotAvatar } from '../components/BotAvatar'
import { PhoneFrame } from '../components/PhoneFrame'
import {
  AddRound, Back, BellIcon, CameraIcon, CandleIcon, CheckRing,
  DateRangeIcon, LightningIcon, MoneyIcon, QuestionIcon,
  ReinvestIcon, SlidersIcon, TagIcon, TargetIcon, TimeIcon, WorldIcon,
} from '../components/Icon'
import s from './BotSettings.module.css'

type Draft = Omit<Profile, 'bot_id'> & {
  deposit_usdc: number
  min_confidence: number
  max_position_usd: number
  max_trades_per_day: number
  take_profit_pct: number
  stop_loss_pct: number
  max_hold_hours: number
}

const EMPTY: Draft = {
  display_name: '',
  tagline: '',
  prompt: '',
  tags: ['주식'],
  style: '장기 투자',
  // 시장 키. 개별 종목은 고르지 않는다 — 그 안에서 무엇을 살지는 봇이 정한다.
  markets: ['us-nasdaq'],
  session: '정규장 (09:00~15:00)',
  base_currency: 'KRW(원)',
  goal: '수익 극대화',
  risk: '중립',
  model: 'Gemini 3.1 Flash Lite',
  notify: true,
  auto_reinvest: false,
  grant_more_authority: false,
  deposit_usdc: 500,
  min_confidence: 0.8,
  max_position_usd: 50,
  max_trades_per_day: 5,
  take_profit_pct: 5,
  stop_loss_pct: 5,
  max_hold_hours: 24,
}

const STYLE_ICONS = [DateRangeIcon, DateRangeIcon, LightningIcon]

export function BotSettings({ mode }: { mode: 'create' | 'edit' }) {
  const { id = '' } = useParams()
  const nav = useNavigate()

  const { data: meta } = useApi(() => api.meta(), [])
  const { data: existing } = useApi(
    () => (mode === 'edit' ? api.profile(id) : Promise.resolve(null)),
    [id, mode],
  )

  const [d, setD] = useState<Draft>(EMPTY)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [preflight, setPreflight] = useState<DeletePreflight | null>(null)
  const [deleting, setDeleting] = useState(false)
  // 방금 만든 봇과 그 봇에 저장된 지침. 바로 넘어가지 않고 한 번 보여준다.
  const [created, setCreated] = useState<{ bot_id: string; ai: BotAi } | null>(
    null,
  )

  // 수정 모드는 서버 값으로 시작한다. 빈 폼으로 시작하면 저장하는 순간
  // 사용자가 건드리지도 않은 항목이 기본값으로 덮인다.
  useEffect(() => {
    if (!existing) return
    setD((prev) => ({
      ...prev,
      ...existing.profile,
      min_confidence: existing.rulebook.min_confidence,
      max_position_usd: existing.rulebook.max_position_usd,
      max_trades_per_day: existing.rulebook.max_trades_per_day,
      take_profit_pct: existing.rulebook.take_profit_pct,
      stop_loss_pct: existing.rulebook.stop_loss_pct,
      max_hold_hours: existing.rulebook.max_hold_hours,
    }))
  }, [existing])

  const set = <K extends keyof Draft>(k: K, v: Draft[K]) =>
    setD((x) => ({ ...x, [k]: v }))

  const toggleIn = (k: 'tags' | 'markets', v: string) =>
    setD((x) => ({
      ...x,
      [k]: x[k].includes(v) ? x[k].filter((t) => t !== v) : [...x[k], v],
    }))

  /** 고른 시장에 실제로 상장된 종목 — 봇이 이 안에서 고른다. */
  const pickable = useMemo(() => {
    if (!meta) return []
    const out: string[] = []
    for (const m of meta.markets) {
      if (!d.markets.includes(m.key)) continue
      for (const t of m.tickers) if (!out.includes(t)) out.push(t)
    }
    return out
  }, [meta, d.markets])

  const nameLeft = `${d.display_name.length}/20`
  const taglineLeft = `${d.tagline.length}/50`
  const promptLeft = `${d.prompt.length}/500`

  async function submit() {
    setErr(null)
    if (!d.display_name.trim()) return setErr('봇 이름을 입력해주세요.')
    if (pickable.length === 0)
      return setErr('거래 가능한 시장을 하나 이상 골라주세요.')

    setBusy(true)
    try {
      if (mode === 'create') {
        const r = await api.createBot({
          display_name: d.display_name,
          tagline: d.tagline,
          prompt: d.prompt,
          tags: d.tags,
          style: d.style,
          markets: d.markets,
          session: d.session,
          base_currency: d.base_currency,
          goal: d.goal,
          risk: d.risk,
          notify: d.notify,
          auto_reinvest: d.auto_reinvest,
          grant_more_authority: d.grant_more_authority,
          model: d.model,
          owner: d.display_name,
          deposit_usdc: d.deposit_usdc,
          min_confidence: d.min_confidence,
          max_position_usd: d.max_position_usd,
          max_trades_per_day: d.max_trades_per_day,
          take_profit_pct: d.take_profit_pct,
          stop_loss_pct: d.stop_loss_pct,
          max_hold_hours: d.max_hold_hours,
        })
        // 곧바로 넘어가지 않는다. 방금 정한 룰북이 어떤 지침이 되었는지
        // 한 번 보여준 뒤에 봇 화면으로 간다.
        setCreated({ bot_id: r.bot_id, ai: r.ai })
      } else {
        await api.patchBot(id, {
          display_name: d.display_name,
          tagline: d.tagline,
          prompt: d.prompt,
          tags: d.tags,
          style: d.style,
          markets: d.markets,
          session: d.session,
          base_currency: d.base_currency,
          goal: d.goal,
          risk: d.risk,
          notify: d.notify,
          auto_reinvest: d.auto_reinvest,
          grant_more_authority: d.grant_more_authority,
          model: d.model,
          min_confidence: d.min_confidence,
          max_position_usd: d.max_position_usd,
          max_trades_per_day: d.max_trades_per_day,
          take_profit_pct: d.take_profit_pct,
          stop_loss_pct: d.stop_loss_pct,
          max_hold_hours: d.max_hold_hours,
        })
        nav(`/bot/${id}`)
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function openDelete() {
    setErr(null)
    try {
      setPreflight(await api.deletePreflight(id))
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }

  async function confirmDelete() {
    setDeleting(true)
    try {
      await api.deleteBot(id)
      nav('/', { replace: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
      setPreflight(null)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <PhoneFrame>
      <header className={s.head}>
        <button
          type="button"
          className={s.back}
          onClick={() => nav(mode === 'edit' ? `/bot/${id}` : '/')}
          aria-label="뒤로"
        >
          <Back size={20} />
        </button>
        <h1 className={s.title}>AI 봇 설정</h1>
      </header>

      {/* ── 기본 정보 ─────────────────────────────────── */}
      <SectionTitle icon={<SlidersIcon size={22} />} label="기본 정보" />
      <section className={s.card}>
        <div className={s.nameRow}>
          <div className={s.avatarWrap}>
            <BotAvatar size={47} />
            <span className={s.camera}>
              <CameraIcon size={13} />
            </span>
          </div>

          <div className={s.fields}>
            <label className={s.fieldLabel}>이름 설정</label>
            <div className={s.inputPill}>
              <input
                value={d.display_name}
                maxLength={20}
                placeholder="봇 이름"
                onChange={(e) => set('display_name', e.target.value)}
              />
              <span className={s.counter}>{nameLeft}</span>
            </div>

            <label className={s.fieldLabel}>
              한 줄 설명 <em>(선택)</em>
            </label>
            <div className={s.inputPill}>
              <input
                value={d.tagline}
                maxLength={50}
                placeholder="이 봇의 투자 전략을 설명해주세요."
                onChange={(e) => set('tagline', e.target.value)}
              />
              <span className={s.counter}>{taglineLeft}</span>
            </div>
          </div>
        </div>

        <label className={s.blockLabel}>프롬프트 설정</label>
        <div className={s.textareaWrap}>
          <textarea
            value={d.prompt}
            maxLength={500}
            rows={4}
            placeholder="주로 반도체 관련 주식을 매매해주세요. 미 연준 금리 발표를 중점적으로 봐서 투자하고…"
            onChange={(e) => set('prompt', e.target.value)}
          />
          <span className={s.counter}>{promptLeft}</span>
        </div>
        <p className={s.hint}>
          이 문장은 심층 추론 프롬프트에 실립니다. 다만 최종 거부권은 아래
          거래 설정(룰북)에 있습니다 — 프롬프트가 뭐라고 하든 룰북을 넘어서는
          매매는 차단됩니다.
        </p>

        {/* 이 봇이 Gemini 에게 실제로 주는 지침 원문.
            룰북·프로필에서 서버가 조립하므로(app/core/prompts.py), 위 값을
            고쳐 저장하면 이 내용도 같이 바뀐다 — 보이는 지침과 쓰이는
            지침이 어긋날 수 없다. */}
        {existing?.ai && (
          <details className={s.aiBox}>
            <summary>
              AI에게 저장된 지침 보기
              <em>
                {existing.ai.live
                  ? ` · ${existing.ai.model_id} 실추론`
                  : ` · 모의 판단 (${existing.ai.inference_mode})`}
              </em>
            </summary>
            <pre className={s.aiPrompt}>{existing.ai.system_prompt}</pre>
            <p className={s.hint}>
              봇을 만들 때 이 지침이 만들어져 판단할 때마다 함께 전송됩니다.
              위의 이름·프롬프트와 아래 룰북을 고치면 이 문장도 따라 바뀝니다.
            </p>
          </details>
        )}
      </section>

      {/* ── 태그 & 분류 ───────────────────────────────── */}
      <SectionTitle icon={<TagIcon size={22} />} label="태그 & 분류" />
      <section className={s.card}>
        <p className={s.rowLabel}>
          봇 태그 <em>(복수 선택 가능)</em>
        </p>
        <div className={s.chipWrap}>
          {(meta?.tags ?? []).map((t) => (
            <button
              key={t}
              type="button"
              className={`${s.chip} ${d.tags.includes(t) ? s.chipOn : ''}`}
              onClick={() => toggleIn('tags', t)}
            >
              {t}
            </button>
          ))}
        </div>

        <p className={s.rowLabel}>거래 스타일</p>
        <div className={s.chipWrap}>
          {(meta?.styles ?? []).map((t, i) => {
            const I = STYLE_ICONS[i] ?? DateRangeIcon
            return (
              <button
                key={t}
                type="button"
                className={`${s.chipWide} ${d.style === t ? s.chipOn : ''}`}
                onClick={() => set('style', t)}
              >
                <I size={22} />
                {t}
              </button>
            )
          })}
        </div>
      </section>

      {/* ── 거래 설정 ─────────────────────────────────── */}
      <SectionTitle icon={<CandleIcon size={22} />} label="거래 설정" />
      <section className={s.card}>
        {/* 시장만 고른다. 개별 종목은 봇이 뉴스와 추론으로 직접 고르는데,
            그게 이 제품이 '자동'이라고 말할 수 있는 근거다. */}
        <p className={s.rowLabel}>
          거래할 시장 <em>(복수 선택 가능)</em>
        </p>
        <div className={s.marketWrap}>
          {(meta?.markets ?? []).map((m) => {
            const on = d.markets.includes(m.key)
            return (
              <button
                key={m.key}
                type="button"
                className={`${s.market} ${on ? s.marketOn : ''}`}
                disabled={!m.tradable}
                aria-pressed={on}
                onClick={() => toggleIn('markets', m.key)}
              >
                <span className={s.marketName}>
                  {m.flag} {m.name}
                </span>
                <span className={s.marketMeta}>
                  {m.tradable
                    ? `상장 ${m.tickers.length}종 · ${m.tickers.join(' · ')}`
                    : m.note}
                </span>
                {m.tradable && m.desc && (
                  <span className={s.marketDesc}>{m.desc}</span>
                )}
              </button>
            )
          })}
        </div>

        <p className={s.hint}>
          종목은 고르지 않습니다. 봇이 뉴스를 사서 읽고, 1차 스크리닝과 심층
          추론을 거쳐 <b>이 시장 안에서 무엇을 살지 스스로 정합니다.</b>
          {pickable.length > 0 && (
            <>
              {' '}
              지금 선택으로 봇이 고를 수 있는 종목은 {pickable.join(', ')} 입니다.
            </>
          )}
        </p>


        <Row icon={<TimeIcon size={22} />} label="거래 시간">
          <select
            className={s.select}
            value={d.session}
            onChange={(e) => set('session', e.target.value)}
          >
            {(meta?.sessions ?? [d.session]).map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Row>

        <Row icon={<MoneyIcon size={22} />} label="기본 투자 통화">
          <select
            className={s.select}
            value={d.base_currency}
            onChange={(e) => set('base_currency', e.target.value)}
          >
            {(meta?.currencies ?? [d.base_currency]).map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Row>
      </section>

      {/* ── 투자 전략 ─────────────────────────────────── */}
      <SectionTitle icon={<TargetIcon size={22} />} label="투자 전략" />
      <section className={s.card}>
        <Row icon={<WorldIcon size={22} />} label="투자 목표">
          <select
            className={s.select}
            value={d.goal}
            onChange={(e) => set('goal', e.target.value)}
          >
            {(meta?.goals ?? [d.goal]).map((x) => (
              <option key={x}>{x}</option>
            ))}
          </select>
        </Row>

        <Row icon={<WorldIcon size={22} />} label="위험 성향">
          <div className={s.segment} role="radiogroup" aria-label="위험 성향">
            {(meta?.risks ?? []).map((r) => (
              <button
                key={r}
                type="button"
                role="radio"
                aria-checked={d.risk === r}
                className={`${s.seg} ${d.risk === r ? s.segOn : ''}`}
                onClick={() => set('risk', r)}
              >
                {r}
              </button>
            ))}
          </div>
        </Row>

        <Row icon={<WorldIcon size={22} />} label="최대 허용 손실(일)">
          <div className={s.stepper}>
            <button
              type="button"
              onClick={() =>
                set('stop_loss_pct', Math.max(0.5, +(d.stop_loss_pct - 0.5).toFixed(1)))
              }
              aria-label="줄이기"
            >
              <AddRound size={20} className={s.minus} />
            </button>
            <span className="tnum">{d.stop_loss_pct}%</span>
            <button
              type="button"
              onClick={() =>
                set('stop_loss_pct', Math.min(90, +(d.stop_loss_pct + 0.5).toFixed(1)))
              }
              aria-label="늘리기"
            >
              <AddRound size={20} />
            </button>
            <span
              className={s.qmark}
              title="이 값이 그대로 룰북의 손절선이 됩니다. 포지션이 -N% 에 닿으면 자동 청산됩니다."
            >
              <QuestionIcon size={20} />
            </span>
          </div>
        </Row>

        <details className={s.advanced}>
          <summary>집행되는 나머지 룰북 값</summary>
          <div className={s.miniGrid}>
            <MiniNum
              label="익절 (+%)"
              value={d.take_profit_pct}
              step={0.5}
              onChange={(v) => set('take_profit_pct', v)}
            />
            <MiniNum
              label="최대 보유 (시간)"
              value={d.max_hold_hours}
              step={1}
              onChange={(v) => set('max_hold_hours', v)}
            />
            <MiniNum
              label="확신도 하한"
              value={d.min_confidence}
              step={0.05}
              onChange={(v) => set('min_confidence', v)}
            />
            <MiniNum
              label="1회 최대 투입 (USD)"
              value={d.max_position_usd}
              step={5}
              onChange={(v) => set('max_position_usd', v)}
            />
            <MiniNum
              label="일일 최대 거래"
              value={d.max_trades_per_day}
              step={1}
              onChange={(v) => set('max_trades_per_day', v)}
            />
            {mode === 'create' && (
              <MiniNum
                label="예치금 (USDC)"
                value={d.deposit_usdc}
                step={50}
                onChange={(v) => set('deposit_usdc', v)}
              />
            )}
          </div>
        </details>
      </section>

      {/* ── 고급 설정 ─────────────────────────────────── */}
      <SectionTitle icon={<SlidersIcon size={22} />} label="고급 설정" />
      <section className={s.card}>
        <ToggleRow
          icon={<BellIcon size={22} />}
          label="알림 설정"
          on={d.notify}
          onChange={(v) => set('notify', v)}
        />
        <ToggleRow
          icon={<ReinvestIcon size={22} />}
          label="자동 재투자"
          on={d.auto_reinvest}
          onChange={(v) => set('auto_reinvest', v)}
        />
        <ToggleRow
          icon={<CheckRing size={22} />}
          label="더 많은 권한 부여"
          desc="AI가 투자뿐 아니라 스탑로스를 제외한 투자 전략까지 판단해 실행합니다."
          on={d.grant_more_authority}
          onChange={(v) => set('grant_more_authority', v)}
        />
      </section>

      {err && <p className={s.error}>{err}</p>}

      <div className={s.submitWrap}>
        <button
          type="button"
          className={s.submit}
          onClick={submit}
          disabled={busy}
        >
          {busy ? '저장 중…' : mode === 'create' ? '봇 만들기' : '설정 완료하기'}
        </button>

        {mode === 'edit' && (
          <button type="button" className={s.deleteBtn} onClick={openDelete}>
            봇 삭제하기
          </button>
        )}
      </div>

      {/* 생성 직후 — 이 봇이 Gemini 에게 무엇을 지시받는지 먼저 보여준다.
          "만들었습니다" 만 띄우고 넘어가면, 사용자가 정한 값이 실제로
          무엇이 되었는지 확인할 기회가 영영 없다. */}
      {created && (
        <div className={s.sheetBackdrop}>
          <div
            className={s.sheet}
            role="dialog"
            aria-label="봇 생성 완료"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className={s.sheetTitle}>봇이 만들어졌습니다</h3>
            <p className={s.sheetMeta}>
              devnet 지갑 4개가 생성되고 {d.deposit_usdc} USDC가 예치됐습니다.
              아래는 이 봇이 판단할 때마다 Gemini에 함께 보내는 지침입니다 —
              {created.ai.live
                ? ` ${created.ai.model_id} 로 실제 추론합니다.`
                : ` 지금은 모의 판단 모드(${created.ai.inference_mode})입니다.`}
            </p>

            <pre className={s.aiPrompt}>{created.ai.system_prompt}</pre>

            <p className={s.sheetMeta}>
              지침이 뭐라고 하든 최종 거부권은 룰북에 있습니다. 허용 종목·확신도
              하한·투입 상한은 코드가 다시 검사합니다.
            </p>

            <div className={s.sheetActions}>
              <button
                type="button"
                className={s.sheetConfirm}
                onClick={() => nav(`/bot/${created.bot_id}`)}
              >
                봇 열기
              </button>
            </div>
          </div>
        </div>
      )}

      {preflight && (
        <div className={s.sheetBackdrop} onClick={() => setPreflight(null)}>
          <div
            className={s.sheet}
            role="alertdialog"
            aria-label="봇 삭제 확인"
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className={s.sheetTitle}>
              ‘{preflight.name}’ 봇을 삭제할까요?
            </h3>

            {preflight.will_close_first > 0 && (
              <div className={s.sheetBlock}>
                <p className={s.sheetLabel}>
                  열린 포지션 {preflight.will_close_first}개를 먼저 청산합니다
                </p>
                <ul className={s.posList}>
                  {preflight.open_positions.map((p) => (
                    <li key={p.ticker + p.qty}>
                      <span>{p.ticker}</span>
                      <span className="tnum">{fmtQty(p.qty)}</span>
                      <span
                        className={p.pnl_pct >= 0 ? s.up : s.down}
                      >
                        {pctSigned(p.pnl_pct)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <div className={s.sheetBlock}>
              <p className={s.sheetLabel}>지갑에 남은 금액</p>
              <p className={`${s.sheetAmount} tnum`}>
                {krw(preflight.remaining_krw)}
              </p>
              {/* 출금 경로가 없다는 사실을 삭제 직전에 분명히 알린다 */}
              {!preflight.withdrawal_supported && (
                <p className={s.sheetWarn}>{preflight.note}</p>
              )}
            </div>

            <p className={s.sheetMeta}>
              거래 기록 {preflight.fills}건도 목록에서 사라집니다. 되돌릴 수
              없습니다.
            </p>

            <div className={s.sheetActions}>
              <button
                type="button"
                className={s.sheetCancel}
                onClick={() => setPreflight(null)}
                disabled={deleting}
              >
                취소
              </button>
              <button
                type="button"
                className={s.sheetConfirm}
                onClick={confirmDelete}
                disabled={deleting}
              >
                {deleting ? '삭제 중…' : '삭제하기'}
              </button>
            </div>
          </div>
        </div>
      )}
    </PhoneFrame>
  )
}

/* ── 조각들 ─────────────────────────────────────────────── */

function SectionTitle({
  icon,
  label,
}: {
  icon: React.ReactNode
  label: string
}) {
  return (
    <h2 className={s.sectionTitle}>
      <span className={s.sectionIcon}>{icon}</span>
      {label}
    </h2>
  )
}

function Row({
  icon,
  label,
  children,
}: {
  icon: React.ReactNode
  label: string
  children: React.ReactNode
}) {
  return (
    <div className={s.row}>
      <span className={s.rowIcon}>{icon}</span>
      <span className={s.rowName}>{label}</span>
      <div className={s.rowCtl}>{children}</div>
    </div>
  )
}

function ToggleRow({
  icon,
  label,
  desc,
  on,
  onChange,
}: {
  icon: React.ReactNode
  label: string
  desc?: string
  on: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <div className={s.toggleRow}>
      <span className={s.rowIcon}>{icon}</span>
      <div className={s.toggleText}>
        <span className={s.rowName}>{label}</span>
        {desc && <p className={s.toggleDesc}>{desc}</p>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label={label}
        className={`${s.switch} ${on ? s.switchOn : ''}`}
        onClick={() => onChange(!on)}
      >
        <span className={s.knob} />
      </button>
    </div>
  )
}

function MiniNum({
  label,
  value,
  step,
  onChange,
}: {
  label: string
  value: number
  step: number
  onChange: (v: number) => void
}) {
  return (
    <label className={s.mini}>
      <span>{label}</span>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  )
}
