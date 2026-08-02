import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { BotAvatar } from '../components/BotAvatar'
import { BottomNav } from '../components/BottomNav'
import { PhoneFrame } from '../components/PhoneFrame'
import { Back, Settings } from '../components/Icon'
import { SummaryPanel } from './panels/SummaryPanel'
import { TradesPanel } from './panels/TradesPanel'
import { ApisPanel } from './panels/ApisPanel'
import s from './BotDetail.module.css'

const TABS = [
  { key: 'summary', label: '요약' },
  { key: 'trades', label: '거래 내역' },
  { key: 'apis', label: 'API' },
] as const

export type DetailTab = (typeof TABS)[number]['key']

export function BotDetail({ tab }: { tab: DetailTab }) {
  const { id = '' } = useParams()
  const nav = useNavigate()

  // 헤더(이름·모델·배지·정지 여부)는 세 탭이 공유한다. 탭을 옮길 때마다
  // 다시 부르면 헤더가 깜빡이므로 여기서 한 번만 받는다.
  const { data: head } = useApi(() => api.profile(id), [id])

  return (
    <PhoneFrame nav={<BottomNav active="stock" />}>
      <header className={s.head}>
        <button
          type="button"
          className={s.back}
          onClick={() => nav('/')}
          aria-label="뒤로"
        >
          <Back size={20} />
        </button>

        <BotAvatar size={47} />

        <div className={s.who}>
          <p className={s.name}>{head?.profile.display_name || id}</p>
          <p className={s.model}>{head?.profile.model ?? ''}</p>
        </div>

        {head && <span className={s.badge}>{head.badge}</span>}

        <Link to={`/bot/${id}/settings`} className={s.gear} aria-label="봇 설정">
          <Settings size={33} />
        </Link>
      </header>

      {head?.killed && (
        <p className={s.pausedBanner}>
          이 봇은 정지 상태입니다 — 새 매매를 하지 않습니다.
        </p>
      )}

      <nav className={s.tabs} aria-label="봇 상세 탭">
        {TABS.map((t) => (
          <Link
            key={t.key}
            to={`/bot/${id}${t.key === 'summary' ? '' : `/${t.key}`}`}
            className={`${s.tab} ${t.key === tab ? s.tabOn : ''}`}
            aria-current={t.key === tab ? 'page' : undefined}
          >
            {t.label}
          </Link>
        ))}
      </nav>

      {tab === 'summary' && <SummaryPanel botId={id} />}
      {tab === 'trades' && <TradesPanel botId={id} />}
      {tab === 'apis' && <ApisPanel botId={id} />}
    </PhoneFrame>
  )
}
