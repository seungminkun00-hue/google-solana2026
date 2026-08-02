import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, useApi } from '../api/client'
import type { WalletAccount } from '../api/types'
import { BotAvatar } from '../components/BotAvatar'
import { ChargeSheet } from '../components/ChargeSheet'
import { BottomNav } from '../components/BottomNav'
import { PhoneFrame } from '../components/PhoneFrame'
import {
  ArrowDown, ArrowUp, ChatAdd, ChevronDown, Filter, Plus, ProfileIcon,
  Search, Send, Swap, Wallet,
} from '../components/Icon'
import { krw, pct, shortAddress, tone, usd } from '../lib/format'
import s from './Home.module.css'

/** 전 지갑 합계를 뜻하는 가상 항목. 실제 지갑 하나가 아니다. */
const ALL = '__all__'

export function Home() {
  const [account, setAccount] = useState<string>(ALL)
  const [picking, setPicking] = useState(false)
  const [charging, setCharging] = useState(false)

  // 5초 폴링. 스케줄러가 돌면 잔고와 포지션이 실제로 움직이므로,
  // 새로고침 없이 그게 보여야 자동매매라는 말이 성립한다.
  const { data, error, loading } = useApi(() => api.overview(), [], 5000)

  const selected: WalletAccount | null = useMemo(() => {
    if (!data || account === ALL) return null
    return data.wallet.accounts.find((a) => a.logical === account) ?? null
  }, [data, account])

  const shownMicro = selected ? selected.total_micro : data?.wallet.total_micro ?? 0
  const shownKrw = selected ? selected.total_krw : data?.wallet.total_krw ?? 0

  const bots = useMemo(() => {
    if (!data) return []
    return account === ALL
      ? data.bots
      : data.bots.filter((b) => b.bot_id === selected?.bot_id)
  }, [data, account, selected])

  return (
    <PhoneFrame nav={<BottomNav active="stock" />}>
      <header className={s.head}>
        <h1 className={s.title}>
          <span className={s.ai}>AI</span> 트레이딩
        </h1>
        {/* 아이콘 크기는 Figma 그룹 `onboarding icon right`(12:259)에서
            잘라낸 실제 크기다 — 돋보기 13 · 프로필 20×19.5 · 정렬 20×18 */}
        <div className={s.headActions}>
          <button type="button" className={s.searchChip}>
            <Search size={13} />
            <span>검색</span>
          </button>
          <button type="button" className={s.roundBtn} aria-label="내 정보">
            <ProfileIcon size={20} style={{ height: 19.5 }} />
          </button>
          <button type="button" className={s.roundBtn} aria-label="정렬">
            <Filter size={20} style={{ height: 18 }} />
          </button>
        </div>
      </header>

      <section className={s.balance}>
        <div className={s.walletRow}>
          <button
            type="button"
            className={s.walletBtn}
            onClick={() => setPicking((v) => !v)}
            aria-expanded={picking}
          >
            <Wallet size={24} />
            <span className={s.walletName}>
              {selected ? selected.label : '내 지갑'}
            </span>
            <span className={s.walletAddr}>
              {selected
                ? `(${shortAddress(selected.address)})`
                : `(지갑 ${data?.wallet.accounts.length ?? 0}개 합계)`}
            </span>
            <ChevronDown size={10} className={picking ? s.flip : undefined} />
          </button>

          <div className={s.usdRow}>
            <span className={s.approx}>≒</span>
            <span className={`${s.usd} tnum`}>
              {usd(shownMicro / 1_000_000)}
            </span>
            <span className={s.usdcMark} title="원장의 기준 통화">
              USDC
            </span>
          </div>
        </div>

        {picking && data && (
          <ul className={s.picker}>
            <li>
              <button
                type="button"
                className={account === ALL ? s.pickOn : undefined}
                onClick={() => {
                  setAccount(ALL)
                  setPicking(false)
                }}
              >
                <span>전체 지갑</span>
                <span className="tnum">{krw(data.wallet.total_krw)}</span>
              </button>
            </li>
            {data.wallet.accounts.map((a) => (
              <li key={a.logical}>
                <button
                  type="button"
                  className={account === a.logical ? s.pickOn : undefined}
                  onClick={() => {
                    setAccount(a.logical)
                    setPicking(false)
                  }}
                >
                  <span>
                    {a.label}
                    <em className={s.pickAddr}>{shortAddress(a.address, 8)}</em>
                  </span>
                  <span className="tnum">{krw(a.total_krw)}</span>
                </button>
              </li>
            ))}
          </ul>
        )}

        <p className={`${s.total} tnum`}>{krw(shownKrw)}</p>

        {data && (
          <p className={s.fxNote}>
            1 USD = {data.fx.rate.toLocaleString('ko-KR', {
              maximumFractionDigits: 2,
            })}
            원 · {data.fx.source === 'fallback' ? '고정 환율' : '실시간 환율'}
          </p>
        )}
      </section>

      {/* 아이콘 크기는 Figma 노드의 실제 크기다 (1:550=49 · 1:554=35 · 1:558=30).
          키우거나 줄이면 획 굵기까지 같이 변해서 원본과 달라진다. */}
      <section className={s.actions}>
        <button type="button" className={s.action}>
          <Send size={49} />
          <span>출금하기</span>
        </button>
        {/* 충전 — 연결된 팬텀 지갑에서 봇 지갑으로 실제로 옮긴다.
            위임을 받아뒀으므로 추가 서명이 없다. */}
        <button
          type="button"
          className={s.action}
          onClick={() => setCharging(true)}
        >
          <ChatAdd size={35} />
          <span>충전하기</span>
        </button>
        <button type="button" className={s.action}>
          <Swap size={30} />
          <span>전환하기</span>
        </button>
      </section>

      <section className={s.botsHead}>
        <h2 className={s.sectionTitle}>나의 봇</h2>
        <Link to="/bot/new" className={s.addBtn}>
          <Plus size={22} />
          <span>추가하기</span>
        </Link>
      </section>

      {loading && <p className={s.state}>불러오는 중…</p>}
      {error && <p className={`${s.state} ${s.err}`}>{error}</p>}

      {data && bots.length === 0 && (
        <p className={s.state}>
          아직 봇이 없습니다. <Link to="/bot/new">추가하기</Link>로 첫 봇을
          만들어 보세요.
        </p>
      )}

      <ul className={s.grid}>
        {bots.map((b) => {
          const t = tone(b.return_pct)
          return (
            <li key={b.bot_id}>
              <Link to={`/bot/${b.bot_id}`} className={s.card}>
                <div className={s.cardTop}>
                  <BotAvatar size={47} />
                  <span className={s.badge}>{b.badge}</span>
                </div>

                <p className={s.botName}>{b.name}</p>
                <p className={s.botModel}>{b.model}</p>

                <p className={`${s.botValue} tnum`}>{krw(b.value_krw)}</p>
                <p className={`${s.change} ${s[t]}`}>
                  {b.return_pct == null ? (
                    <span className={s.noData}>거래 전</span>
                  ) : (
                    <>
                      {t === 'down' ? (
                        <ArrowDown size={18} />
                      ) : (
                        <ArrowUp size={18} />
                      )}
                      <span className="tnum">{pct(Math.abs(b.return_pct))}</span>
                    </>
                  )}
                </p>

                {b.killed && <span className={s.paused}>일시 정지됨</span>}
              </Link>
            </li>
          )
        })}
      </ul>

      {charging && (
        <ChargeSheet
          bots={data?.bots ?? []}
          defaultBotId={selected?.bot_id}
          onClose={() => setCharging(false)}
        />
      )}

      {data && (
        <p className={s.modes}>
          원장 {data.ledger_mode} · 추론{' '}
          {data.inference_live ? '실추론' : data.inference_mode} · 시세{' '}
          {data.price_live ? '실시세(KIS)' : data.price_source}
        </p>
      )}
    </PhoneFrame>
  )
}
