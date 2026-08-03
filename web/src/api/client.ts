import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  Apis, BotAi, ChatReply, DeletePreflight, Events, Meta, Overview,
  ProfileBundle, Progress, RunResult, Runtime, SellResult, Summary, Trades,
} from './types'

// vite.config.ts 가 /api 를 백엔드로 프록시한다.
const BASE = import.meta.env.VITE_API_BASE ?? '/api'

// 자금이 움직이는 라우트(봇 생성·수정·정지)에는 관리자 토큰이 필요하다.
// 백엔드 기본값은 config.py 의 "dev-token".
//
// ⚠️ 브라우저에 토큰을 두는 것은 데모 구성이다. 번들에 그대로 들어가므로
//    누구나 볼 수 있다. 실서비스라면 사용자 세션으로 인증하고 서버가
//    관리자 권한을 대신 행사해야 한다.
const ADMIN_TOKEN = import.meta.env.VITE_ADMIN_TOKEN ?? 'dev-token'

/**
 * 이 브라우저를 가리키는 값. 서버는 이걸로 '누가 만든 봇인가'를 가른다
 * (app/core/session.py). 로그인이 아니라 격리용이다 — 심사 링크에
 * 회원가입을 붙일 수는 없다.
 *
 * localStorage 에 두는 이유: 탭을 닫았다 열어도 자기 봇이 남아 있어야
 * 한다. 세션스토리지면 새 탭마다 남처럼 보인다.
 */
const SESSION_KEY = 'ce.session'

export function sessionId(): string {
  let v = localStorage.getItem(SESSION_KEY)
  if (!v) {
    v = crypto.randomUUID().replace(/-/g, '').slice(0, 24)
    localStorage.setItem(SESSION_KEY, v)
  }
  return v
}

export class ApiError extends Error {
  // 생성자 파라미터 프로퍼티는 tsconfig 의 erasableSyntaxOnly 에 걸린다
  // (타입만 지워서 JS가 되지 않는 문법). 필드를 따로 선언한다.
  status: number
  detail?: unknown

  constructor(status: number, message: string, detail?: unknown) {
    super(message)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init?: RequestInit & { admin?: boolean },
): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as object) }
  if (init?.body) headers['Content-Type'] = 'application/json'
  if (init?.admin) headers['X-Admin-Token'] = ADMIN_TOKEN
  // 모든 요청에 붙인다. 조회도 마찬가지 — 남의 봇은 애초에 안 보여야 한다.
  headers['X-Session'] = sessionId()

  const res = await fetch(`${BASE}${path}`, { ...init, headers })
  const text = await res.text()

  // JSON 이 아닐 수 있다 — 이걸 전제하지 않은 것이 버그였다.
  //
  // [버그 2026-08-03] 예전에는 `JSON.parse(text)` 를 그냥 했다. 서버가
  // 처리되지 않은 예외로 죽으면 FastAPI 는 **plain text** "Internal Server
  // Error" 를 돌려주는데, 그걸 파싱하다 터지면서 사용자에게는
  //     Unexpected token 'I', "Internal S"... is not valid JSON
  // 이 떴다. 원인과 아무 상관 없는 메시지라, 실제로 무슨 일이 일어났는지가
  // 화면에서 완전히 사라졌다. 서버 500 을 전부 이 한 문장으로 덮은 셈이다.
  let data: unknown = null
  let parseFailed = false
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      parseFailed = true
    }
  }
  const d = (data as { detail?: unknown })?.detail ?? data

  if (!res.ok) {
    // FastAPI 는 에러를 {detail: ...} 로 싼다. detail 이 dict 면
    // 서버가 넣어둔 hint 가 사용자에게 보여줄 만한 문장이다.
    const obj = d as { error?: string; hint?: string } | null
    const msg = parseFailed
      ? `서버 오류 (${res.status}): ${text.trim().slice(0, 160)}`
      : typeof d === 'string'
        ? d
        : (obj?.error ?? obj?.hint ?? `요청 실패 (${res.status})`)
    throw new ApiError(res.status, msg, d)
  }
  if (parseFailed) {
    // 200 인데 JSON 이 아니다. 라우트를 못 찾아 SPA 의 index.html 이
    // 돌아온 경우가 대표적이다 — 조용히 넘기면 화면이 빈 채로 남는다.
    throw new ApiError(res.status,
      `서버가 JSON 이 아닌 응답을 보냈습니다: ${text.trim().slice(0, 160)}`)
  }
  return data as T
}

export const api = {
  overview: () => request<Overview>('/ui/overview'),
  summary: (id: string, range = '3m') =>
    request<Summary>(`/ui/bots/${id}/summary?range=${range}`),
  trades: (id: string, limit = 50) =>
    request<Trades>(`/ui/bots/${id}/trades?limit=${limit}`),
  apis: (id: string) => request<Apis>(`/ui/bots/${id}/apis`),
  meta: () => request<Meta>('/ui/meta'),
  profile: (id: string) => request<ProfileBundle>(`/ui/bots/${id}/profile`),

  // history 를 함께 보낸다. 서버는 대화를 저장하지 않는다 — 목록을 이미
  // 화면이 들고 있고, 봇 하나에 사람이 하나인 것도 아니기 때문이다.
  chat: (
    id: string,
    message: string,
    history: { role: 'user' | 'model'; text: string }[] = [],
  ) =>
    request<ChatReply>(`/ui/bots/${id}/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, history }),
    }),

  chatSuggestions: (id: string) =>
    request<{ suggestions: string[] }>(`/ui/bots/${id}/chat/suggestions`),

  // 체결 알림. since 를 안 주면 지금 위치만 알려주고 과거는 안 보낸다 —
  // 화면을 열자마자 지난 체결이 우르르 뜨는 것을 막는다.
  events: (since?: number) =>
    request<Events>(`/ui/events${since === undefined ? '' : `?since=${since}`}`),

  // 봇을 한 사이클 돌린다. 뉴스 구매 → 스크리닝 → 심층추론 → 룰북 →
  // 체결까지 실제로 일어나고, devnet 트랜잭션도 그만큼 실제로 남는다.
  run: (id: string, attempts = 2) =>
    request<RunResult>(`/ui/bots/${id}/run?attempts=${attempts}`, {
      method: 'POST',
      admin: true,
    }),

  // 진행 중인 사이클의 단계. run() 이 도는 동안 화면이 이걸 반복해 부른다.
  // 사이클 응답은 다 끝나야 오는데 devnet 에서는 30~120초라, 그때까지
  // 화면이 비어 있으면 "눌렀는데 아무 일도 안 일어난다" 로 보인다.
  progress: (id: string, since = 0) =>
    request<Progress>(`/ui/bots/${id}/progress?since=${since}`),

  // 실행 상태 — 목업 위 상태 램프가 폴링한다.
  runtime: () => request<Runtime>('/ui/runtime'),

  // 자동매매 켜기/끄기. 켜면 이 세션의 정지된 봇도 함께 되살린다.
  scheduler: (on: boolean, intervalSeconds?: number) =>
    request<Runtime & { woke?: string[] }>(
      `/ui/runtime/scheduler?on=${on}` +
        (intervalSeconds ? `&interval_seconds=${intervalSeconds}` : ''),
      { method: 'POST', admin: true },
    ),

  // 단기 매매 모드. 익절·손절을 얕게, 보유시간을 짧게 바꾼다.
  scalp: (on: boolean) =>
    request<Runtime & { changed?: unknown[] }>(`/ui/runtime/scalp?on=${on}`, {
      method: 'POST',
      admin: true,
    }),

  // 매도·정산. all=false 면 룰북 조건(익절·손절·보유시간)에 걸린 것만,
  // all=true 면 조건과 무관하게 전량 청산한다.
  sell: (id: string, all = false) =>
    request<SellResult>(`/ui/bots/${id}/sell?all=${all}`, {
      method: 'POST',
      admin: true,
    }),

  pause: (id: string, on: boolean) =>
    request<{ bot_id: string; killed: boolean }>(
      `/ui/bots/${id}/pause?on=${on}`,
      { method: 'POST', admin: true },
    ),

  // 생성 응답에는 그 봇이 Gemini 에게 주게 될 지침(ai)이 함께 온다.
  // 방금 정한 룰북이 어떤 문장이 되었는지 그 자리에서 보여주기 위해서다.
  createBot: (body: unknown) =>
    request<{ bot_id: string; ai: BotAi }>('/ui/bots', {
      method: 'POST',
      body: JSON.stringify(body),
      admin: true,
    }),

  patchBot: (id: string, body: unknown) =>
    request<ProfileBundle>(`/ui/bots/${id}/profile`, {
      method: 'PATCH',
      body: JSON.stringify(body),
      admin: true,
    }),

  deletePreflight: (id: string) =>
    request<DeletePreflight>(`/ui/bots/${id}/delete-preflight`),

  // force 는 청산이 안 되는 포지션 때문에 봇이 영영 안 지워질 때의 비상구다.
  // 기록만 버리고 삭제한다 — 그 종목 토큰은 봇 지갑에 남는다.
  deleteBot: (id: string, force = false) =>
    request<{
      deleted: string
      closed_positions: number
      abandoned_positions: { ticker: string; receipt_id: string }[]
    }>(
      `/ui/bots/${id}?close_positions=true&force=${force}`,
      { method: 'DELETE', admin: true },
    ),

  // 봇을 실제로 한 사이클 돌린다. 데모에서 "지금 일해봐" 버튼용.
  runCycle: (id: string) =>
    request<{ log: { step: string }[] }>(`/bots/${id}/cycle`, {
      method: 'POST',
      admin: true,
    }),

  closeAll: (id: string) =>
    request<{ closed: number }>(`/bots/${id}/close-all`, {
      method: 'POST',
      admin: true,
    }),
}

type State<T> = {
  data: T | null
  error: string | null
  loading: boolean
}

/**
 * 조회 훅. 폴링 간격을 주면 그 주기로 다시 불러온다.
 *
 * 새로 고칠 때 data 를 비우지 않는다 — 비우면 폴링마다 화면이
 * 로딩 상태로 깜빡이고, 금액이 사라졌다 나타난다.
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: unknown[],
  intervalMs?: number,
): State<T> & { reload: () => void } {
  const [state, setState] = useState<State<T>>({
    data: null,
    error: null,
    loading: true,
  })
  const alive = useRef(true)
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const run = useCallback(async () => {
    try {
      const data = await fetcherRef.current()
      if (alive.current) setState({ data, error: null, loading: false })
    } catch (e) {
      if (alive.current) {
        setState((s) => ({
          ...s,
          error: e instanceof Error ? e.message : String(e),
          loading: false,
        }))
      }
    }
  }, [])

  useEffect(() => {
    alive.current = true
    setState((s) => ({ ...s, loading: s.data === null }))
    run()
    if (!intervalMs) return () => { alive.current = false }
    const t = setInterval(run, intervalMs)
    return () => {
      alive.current = false
      clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, intervalMs])

  return { ...state, reload: run }
}
