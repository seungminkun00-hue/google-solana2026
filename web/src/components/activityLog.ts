import { createContext, useContext } from 'react'

/**
 * 실행 로그 — 앱과 지갑 시연이 함께 쓰는 하나의 기록.
 *
 * [왜 하나로 모으나]
 * 예전에는 로그가 「심사위원 지갑 시연」 탭에만 있었다. 그래서 앱에서
 * [지금 일해보기] 를 누르면 무슨 일이 일어나는지는 그 카드 안에만
 * 잠깐 보였고, 안내 탭을 보고 있으면 아무것도 안 보였다.
 *
 * 이제 두 경로가 같은 로그로 흐른다. 어느 탭을 보고 있든 오른쪽에
 * 계속 떠 있으므로, 심사위원은 '지금 무엇이 일어나는 중인지'를
 * 놓치지 않는다.
 *
 * group 은 그 줄이 어느 경로에서 왔는지다.
 *   cycle — 앱의 [지금 일해보기] (봇 사이클)
 *   judge — 지갑 시연의 [매수 실행] (위임 인출 + 봇 사이클)
 */
export type LogGroup = 'cycle' | 'judge'

export type LogEntry = {
  id: number
  group: LogGroup
  /** 단계 이름. 서버가 보낸 값 그대로 — 이름을 못 붙였다고 감추지 않는다. */
  step: string
  /** 시작 후 경과(초). 지갑 시연 스트림만 준다. */
  t?: number
  tx?: string
  explorer?: string
  /** step·t·tx·explorer 를 뺀 나머지. 화면이 그대로 펼쳐 보여준다. */
  rest: Record<string, unknown>
}

export type ActivityLog = {
  entries: LogEntry[]
  /** 한 줄 추가. 여러 줄이면 push 를 여러 번 부르지 말고 배열로. */
  push: (group: LogGroup, rows: Record<string, unknown>[]) => void
  /** 새 실행이 시작될 때 구분선을 넣는다. */
  begin: (group: LogGroup, label: string) => void
  clear: () => void
  /** 지금 무언가 돌고 있는가. 패널 머리글의 표시등용. */
  busy: boolean
  setBusy: (v: boolean) => void
}

export const ActivityCtx = createContext<ActivityLog | null>(null)

/** provider 밖에서도 안전하다 — 없으면 아무데도 안 쌓인다. */
export function useActivity(): ActivityLog {
  return (
    useContext(ActivityCtx) ?? {
      entries: [],
      push: () => {},
      begin: () => {},
      clear: () => {},
      busy: false,
      setBusy: () => {},
    }
  )
}

/** 단계 이름의 사람용 이름. 모르는 단계는 원문 그대로 보여준다 —
 *  이름을 못 붙였다고 감추면 실제로 일어난 일이 화면에서 사라진다. */
export const STEP_LABEL: Record<string, string> = {
  // 지갑 시연 전용
  'delegate-check': '위임 한도 확인',
  'pull-start': '심사위원 지갑에서 인출 시도',
  'pull-done': '인출 완료 — 온체인 확정',
  preflight: '시연 준비 — 일일 카운터 리셋',
  'close-open': '총 노출 한도 도달 — 기존 포지션 청산',
  'close-done': '청산 완료',
  retry: '재시도',
  'summary-fill': '① 주식 토큰 매수 결과',
  'summary-apis': '② API 호출당 결제 내역',
  'cycle-start': '봇 사이클 시작',
  done: '완료',
  blocked: '중단',
  error: '오류',
  'stream-failed': '스트림 연결 실패 — 서버가 응답하지 않음',
  // 대화로 시킨 매매. 거절도 남긴다 — 안 된 이유가 안 보이는 것이
  // 안 된 것보다 나쁘다.
  'chat-order': '대화 지시 — 주문 체결',
  'chat-order-rejected': '대화 지시 — 실행되지 않음',
  // 사이클 단계 (양쪽 공통 — 지갑 시연은 앞에 cycle: 이 붙는다)
  'budget-check': '예산 점검',
  scout: '뉴스 구매 + 1차 스크리닝',
  analyst: '시그널 구매 + 심층 추론',
  'external-sale': '외부 에이전트에 판단 판매',
  replenish: '인지비용 보충 — 만다트 심사',
  rulebook: '룰북 게이트',
  'capital-invoice': '매매자금 청구 — 만다트 심사',
  executor: '체결 — USDC 지불, 주식 토큰 수령',
  'position-open': '포지션 보유 시작',
  'cycle:budget-check': '예산 점검',
  'cycle:scout': '뉴스 구매 + 1차 스크리닝 (x402)',
  'cycle:analyst': '시그널 구매 + 심층 추론 (x402)',
  'cycle:external-sale': '외부 에이전트에 판단 판매 (x402)',
  'cycle:replenish': '인지비용 보충 — 만다트 심사',
  'cycle:rulebook': '룰북 게이트',
  'cycle:capital-invoice': '매매자금 청구 — 만다트 심사',
  'cycle:executor': '체결 — USDC 지불, 주식 토큰 수령',
  'cycle:position-open': '포지션 보유 시작',
  // 구분선
  '—': '',
}
