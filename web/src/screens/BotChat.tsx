import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, useApi } from '../api/client'
import { BotAvatar } from '../components/BotAvatar'
import { PhoneFrame } from '../components/PhoneFrame'
import { Back, Settings } from '../components/Icon'
import { useActivity } from '../components/activityLog'
import s from './BotChat.module.css'

type Msg = {
  id: number
  from: 'bot' | 'me'
  text: string
  source?: string
  /** 서버가 이 질문을 주식·투자 범위 밖으로 봤는가 */
  offTopic?: boolean
  pending?: boolean
}

const GREETING =
  '안녕하세요! 매매 봇이에요. 보유 종목·손익·매매 내역·룰북 설정처럼 ' +
  '투자에 관한 것을 물어봐 주세요. 그 밖의 주제는 답변하지 않습니다.'

export function BotChat() {
  const { id = '' } = useParams()
  const nav = useNavigate()
  const { data: head } = useApi(() => api.profile(id), [id])

  const [msgs, setMsgs] = useState<Msg[]>([
    { id: 0, from: 'bot', text: GREETING },
  ])
  const [draft, setDraft] = useState('')
  const [suggestions, setSuggestions] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)
  const seq = useRef(1)
  // 대화로 시킨 매매도 [지금 일해보기] 와 같은 실행 로그에 남긴다.
  // 예전에는 여기서만 안 보냈다 — 백엔드는 진짜로 체결하고 응답에
  // trade/order 를 실어 보내는데 이 화면이 그걸 그냥 버렸다. 그래서
  // "대화로 매수 지시한 건 왜 로그에 안 뜨지?" 가 됐다.
  const activity = useActivity()

  // 예시 질문만 받아온다. 예전에는 '__init__' 을 대화로 보냈는데, 그건
  // 화면을 열 때마다 뜻 없는 질문 하나를 모델에 던지는 셈이었다.
  useEffect(() => {
    api
      .chatSuggestions(id)
      .then((r) => setSuggestions(r.suggestions))
      .catch(() => {})
  }, [id])

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [msgs])

  async function send(text: string) {
    const t = text.trim()
    if (!t || busy) return

    const myId = seq.current++
    const botId = seq.current++
    setMsgs((m) => [
      ...m,
      { id: myId, from: 'me', text: t },
      { id: botId, from: 'bot', text: '…', pending: true },
    ])
    setDraft('')
    setBusy(true)

    // 앞선 대화를 함께 보낸다. 첫 인사말은 서버가 준 게 아니라 화면 문구라
    // 빼고, 오류 말풍선도 뺀다 — 그것들을 맥락으로 주면 봇이 자기가 하지
    // 않은 말을 자기 말로 알게 된다.
    const history = msgs
      .filter((m) => !m.pending && m.source !== 'error' && m.id !== 0)
      .slice(-8)
      .map((m) => ({ role: m.from === 'me' ? 'user' : 'model', text: m.text }) as const)

    try {
      const r = await api.chat(id, t, [...history])
      setMsgs((m) =>
        m.map((x) =>
          x.id === botId
            ? {
                ...x,
                text: r.reply,
                source: r.source,
                offTopic: r.on_topic === false,
                pending: false,
              }
            : x,
        ),
      )
      if (r.suggestions?.length) setSuggestions(r.suggestions)

      // 서버가 이 대화를 '주문' 으로 읽었을 때만 로그를 남긴다. 모든
      // 대화를 남기면 로그가 잡담으로 차서 정작 돈이 움직인 줄이 묻힌다.
      if (r.order) {
        const who = head?.profile.display_name || id
        activity.begin('cycle', `앱 · 대화 지시 — ${who}`)
        activity.push('cycle', [
          {
            step: r.order.executed ? 'chat-order' : 'chat-order-rejected',
            지시: r.order.instruction ?? t,
            종목: r.order.ticker ?? '—',
            방향: r.order.side === 'sell' ? '매도' : '매수',
            결과: r.order.note,
            ...(r.trade ?? {}),
          },
        ])
      }
    } catch (e) {
      setMsgs((m) =>
        m.map((x) =>
          x.id === botId
            ? {
                ...x,
                text: e instanceof Error ? e.message : String(e),
                pending: false,
                source: 'error',
              }
            : x,
        ),
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <PhoneFrame className={s.screen} column flush>
      <header className={s.head}>
        <button
          type="button"
          className={s.back}
          onClick={() => nav(`/bot/${id}`)}
          aria-label="뒤로"
        >
          <Back size={20} />
        </button>
        <BotAvatar size={47} />
        <div className={s.who}>
          <p className={s.name}>{head?.profile.display_name || id}</p>
          {/* 지금 진짜 모델이 답하는지 한눈에.
              '추론 mock' 배지를 화면 아래에서 찾아야만 알 수 있으면,
              대화 중에는 그게 실추론인지 알 방법이 없다. */}
          {head?.ai.live ? (
            <p className={`${s.model} ${s.liveRow}`}>
              <span className={s.liveDot} aria-hidden />
              {head.ai.model_id} · 실시간 연결됨
            </p>
          ) : (
            <p className={s.model}>
              {head ? `${head.profile.model} · 모의 판단` : ''}
            </p>
          )}
        </div>
        {head && <span className={s.badge}>{head.badge}</span>}
        <button
          type="button"
          className={s.gear}
          onClick={() => nav(`/bot/${id}/settings`)}
          aria-label="봇 설정"
        >
          <Settings size={33} />
        </button>
      </header>

      <ul className={s.thread}>
        {msgs.map((m) => (
          <li
            key={m.id}
            className={`${s.row} ${m.from === 'me' ? s.mine : s.theirs}`}
          >
            <div className={`${s.bubble} ${m.pending ? s.pending : ''}`}>
              {m.text}
            </div>
            {m.source && m.source !== 'error' && (
              <span className={s.src}>
                {/* 답한 주체를 그대로 밝힌다. 모델 ID 면 실추론이고,
                    'state' 면 모델 없이 원장에서 답을 만든 것이다. */}
                {m.source === 'state' ? '원장 기반 응답' : m.source}
                {m.offTopic && ' · 주식 외 주제라 답변 안 함'}
              </span>
            )}
          </li>
        ))}
        <div ref={endRef} />
      </ul>

      <div className={s.dock}>
        <ul className={s.chips}>
          {suggestions.map((q) => (
            <li key={q}>
              <button type="button" onClick={() => send(q)} disabled={busy}>
                {q}
              </button>
            </li>
          ))}
        </ul>

        <form
          className={s.inputRow}
          onSubmit={(e) => {
            e.preventDefault()
            send(draft)
          }}
        >
          <input
            className={s.input}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="메시지를 입력해주세요"
            aria-label="메시지"
          />
          <button
            type="submit"
            className={s.sendBtn}
            disabled={busy || !draft.trim()}
            aria-label="보내기"
          >
            <span className={s.sendArrow} />
          </button>
        </form>
      </div>
    </PhoneFrame>
  )
}
