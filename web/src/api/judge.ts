/**
 * 심사위원 시연 창구 클라이언트.
 *
 * 앱 화면이 쓰는 client.ts 와 분리해 둔다. 저쪽은 '앱이 보여주는 것'이고
 * 이쪽은 '시연을 위해 아이폰 바깥에 붙인 것'이라 수명이 다르다.
 * 심사가 끝나면 이 파일과 JudgePanel 만 지우면 앱은 그대로 남는다.
 */
import { sessionId } from './client'

const BASE = import.meta.env.VITE_API_BASE ?? '/api'

export type JudgeStatus = {
  registered: boolean
  address?: string
  exists?: boolean
  balance?: number
  delegate?: string | null
  allowance?: number
}

export type BuyEvent = {
  step: string
  t: number
  [k: string]: unknown
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: {
      'X-Session': sessionId(),
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const d = data?.detail ?? data
    throw new Error(typeof d === 'string' ? d : `요청 실패 (${res.status})`)
  }
  return data as T
}

export const judgeApi = {
  status: async (): Promise<JudgeStatus> => {
    const res = await fetch(`${BASE}/judge/status`, {
      headers: { 'X-Session': sessionId() },
    })
    if (!res.ok) throw new Error(`상태 조회 실패 (${res.status})`)
    return res.json()
  },

  register: (address: string) =>
    post<JudgeStatus & { address: string }>('/judge/register', { address }),

  faucet: (amount?: number) =>
    post<{ amount: number; tx: string; explorer: string }>(
      `/judge/faucet${amount ? `?amount=${amount}` : ''}`,
    ),

  /** 봇 트레저리에 입금만 한다 (매매 없음). 위임 한도 안에서 서명 없이. */
  deposit: (botId: string, amount: number) =>
    post<{
      amount_usd: number
      tx: string
      explorer: string
      judge_balance: number
      allowance_left: number
      bot_treasury: number
    }>('/judge/deposit', { bot_id: botId, amount }),

  approveTx: (botId: string, amount: number) =>
    post<{ transaction: string; delegate: string; amount: number }>(
      '/judge/approve-tx',
      { bot_id: botId, amount },
    ),

  /**
   * 매도·정산. 청산한 돈이 심사위원 지갑으로 돌아온다.
   * 스트림 모양은 buy 와 같아서 같은 로그가 그대로 받는다.
   */
  sell(
    botId: string,
    onEvent: (e: BuyEvent) => void,
    onEnd: () => void,
  ): () => void {
    return stream(
      `${BASE}/judge/sell?bot_id=${encodeURIComponent(botId)}` +
        `&session=${encodeURIComponent(sessionId())}`,
      onEvent,
      onEnd,
    )
  },

  /**
   * 매수. SSE 라 EventSource 를 쓴다.
   *
   * fetch 로 읽지 않는 이유: 스트림을 손으로 파싱해야 하고 재연결도
   * 직접 만들어야 한다. 브라우저가 이미 하는 일을 다시 만들 이유가 없다.
   * 정리 함수를 돌려주므로 호출부가 반드시 닫아야 한다 — 안 닫으면
   * 백엔드 제너레이터가 계속 살아 있는다.
   */
  buy(
    botId: string,
    draw: number,
    onEvent: (e: BuyEvent) => void,
    onEnd: () => void,
  ): () => void {
    return stream(
      `${BASE}/judge/buy?bot_id=${encodeURIComponent(botId)}&draw=${draw}` +
        `&session=${encodeURIComponent(sessionId())}`,
      onEvent,
      onEnd,
    )
  },
}

/** SSE 한 벌. 매수·매도가 같은 규칙으로 끝나므로 한 곳에 둔다. */
function stream(
  url: string,
  onEvent: (e: BuyEvent) => void,
  onEnd: () => void,
): () => void {
  const es = new EventSource(url)

  es.onmessage = (m) => {
    const data = JSON.parse(m.data) as BuyEvent
    onEvent(data)
    // 서버가 스트림을 끝내면 EventSource 는 재연결을 시도한다.
    // 종료 신호를 받은 쪽에서 먼저 닫아야 같은 작업이 또 돌지 않는다.
    if (data.step === 'done' || data.step === 'error' || data.step === 'blocked') {
      es.close()
      onEnd()
    }
  }
  es.onerror = () => {
    es.close()
    onEnd()
  }
  return () => {
    es.close()
    onEnd()
  }
}
