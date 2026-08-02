// 백엔드 app/ui.py 의 응답 모양. 서버가 바뀌면 여기부터 고친다.
//
// 금액은 두 벌로 온다: `_micro`(마이크로 USDC 정수 원본)와 `_krw`(환산된 원).
// 화면은 원화를 쓰고, 정확한 비교·합산이 필요하면 micro 를 쓴다.
// 프론트에서 다시 나누고 반올림하면 서버가 계산한 값과 어긋난다.

export type Fx = {
  rate: number
  source: string
  updated: string | null
  ts: number
}

export type BotHeader = {
  bot_id: string
  owner: string
  name: string
  model: string
  badge: string
  killed: boolean
}

export type BotCard = BotHeader & {
  value_micro: number
  value_krw: number
  /** 투입원가 대비 총 수익률. 아직 투입이 없으면 null — 0%가 아니다. */
  return_pct: number | null
  open_positions: number
  self_funding: boolean
}

export type WalletAccount = {
  bot_id: string
  label: string
  logical: string
  address: string | null
  total_micro: number
  total_krw: number
}

export type Overview = {
  wallet: {
    total_micro: number
    total_krw: number
    total_usd: number
    accounts: WalletAccount[]
  }
  fx: Fx
  bots: BotCard[]
  ledger_mode: string
  inference_mode: string
  /** 선언한 모드가 아니라 '실제로 Gemini 를 부를 수 있는가'. 키가 없으면 false. */
  inference_live: boolean
  price_source: string
  /** 실제로 실시세를 받아오고 있는가 (KIS 키가 있고 mock 이 아닐 때). */
  price_live: boolean
}

export type Highlight = { text: string; tone: 'up' | 'down' | 'brand' }

export type Report = {
  text: string
  generated_at: number | null
  empty: boolean
  ticker?: string
  company?: string
  flag?: string
  side?: string | null
  confidence?: number
  inference_mode: string | null
  degraded: boolean
  sources: Record<string, string>
  highlights: Highlight[]
}

export type EquityPoint = { ts: number; total_micro: number }

export type Equity = {
  range: string
  points: EquityPoint[]
  window_return_pct: number | null
  span_seconds: number
  /** 요청한 기간을 데이터가 실제로 덮는가. false면 화면이 '누적 중'을 알린다. */
  covers_range: boolean
}

export type Holding = {
  ticker: string
  company: string
  /** 어느 나라 주식인가 — 🇰🇷 🇺🇸 🇯🇵. 없으면 빈 문자열. */
  flag: string
  value_micro: number
  basis_micro: number
  qty: number
  qty_display: number
  weight_pct: number
  value_krw: number
  color: string
}

export type Summary = BotHeader & {
  report: Report
  equity: Equity
  ranges: string[]
  performance: {
    current_micro: number
    current_krw: number
    basis_micro: number
    basis_krw: number
    unrealized_micro: number
    unrealized_krw: number
    unrealized_pct: number | null
    total_return_pct: number | null
    realized_micro: number
    cash_micro: number
    market_micro: number
  }
  holdings: Holding[]
  wallets: Record<string, number>
  fx: Fx
}

export type TradeRow = {
  fill_id: string
  ts: number
  ticker: string
  company: string
  flag: string
  side: 'buy' | 'sell'
  side_ko: string
  qty: number
  price_micro: number
  price_krw: number
  gross_micro: number
  gross_krw: number
  pnl_micro: number | null
  reason: string
  tx: string
  /** devnet 서명일 때만 채워진다. mock 증빙에는 볼 트랜잭션이 없다. */
  explorer: string | null
}

export type Trades = BotHeader & {
  ledger_mode: string
  summary: {
    total_fills: number
    buys: number
    sells: number
    avg_hold_hours: number | null
    fill_rate_pct: number | null
    win_rate_pct: number | null
    decisions: number
    settled: number
    open_positions: number
  }
  rows: TradeRow[]
  fx: Fx
}

export type ConnectedApi = {
  key: string
  name: string
  tags: string[]
  paysh: boolean
  kind: string
  calls: number
  spend_micro: number
  calls_pct: number
  spend_pct: number
  spend_krw: number
}

export type RecommendedApi = {
  key: string
  name: string
  tags: string[]
  price_note: string
  desc: string
  adapter: string
  ready: boolean
}

/** x402 결제 한 건. 이 서명이 곧 '호출당 결제'의 증거다. */
export type ApiCall = {
  ts: number
  name: string
  key: string
  resource: string
  amount_micro: number
  amount_krw: number
  /** 그 호출에 실제로 답한 모델 ID. 폴백했으면 "mock". 추론 라우트에만 있다. */
  model: string
  tx: string
  explorer: string | null
}

export type Apis = BotHeader & {
  recent: ApiCall[]
  ledger_mode: string
  summary: {
    calls: number
    spend_micro: number
    spend_krw: number
    calls_per_day: number
    spend_per_day_micro: number
    spend_per_day_krw: number
    span_days: number
  }
  connected: ConnectedApi[]
  recommended: RecommendedApi[]
  price_table: Record<string, number>
  fx: Fx
}

export type ChatReply = {
  reply: string
  /** 답한 주체. 모델 ID("gemini-3.1-flash-lite") 이거나 "state"(원장 기반). */
  source: string
  /** 질문이 주식·투자 범위 안이었는가. false 면 봇이 답변을 거절한 것이다. */
  on_topic?: boolean
  /** 대화로 지시한 매매가 실제로 집행됐으면 그 결과. */
  trade?: Record<string, unknown> | null
  grounded?: string
  suggestions: string[]
}

/** 상단 팝업 알림 한 건 (체결). */
export type FillEvent = {
  seq: number
  kind: 'fill'
  ts: number
  bot_id: string
  bot_name: string
  ticker: string
  company: string
  flag: string
  side: 'buy' | 'sell'
  qty: number
  price_micro: number
  gross_micro: number
  gross_krw: number
  pnl_micro: number | null
  reason: string
  tx: string
  explorer: string | null
}

/** 입금 알림. 체결과 같은 자리에 뜬다. */
export type DepositEvent = {
  seq: number
  kind: 'deposit'
  ts: number
  bot_id: string
  bot_name: string
  amount_micro: number
  amount_krw: number
  source: string
  tx: string
  explorer: string | null
}

export type AppEvent = FillEvent | DepositEvent

export type Events = { seq: number; events: AppEvent[] }

/** 봇이 Gemini 에게 주는 지침. 룰북·프로필에서 그때그때 조립된다. */
export type BotAi = {
  system_prompt: string
  model: string
  model_id: string
  live: boolean
  inference_mode: string
}

/** '지금 일해보기' 결과. log 는 사이클이 실제로 밟은 단계 그대로다. */
export type RunResult = {
  bot_id: string
  filled: boolean
  attempts: number
  preflight: Record<string, unknown>[]
  log: Record<string, unknown>[]
  balances: Record<string, number>
}

/** 거래 시장. 사용자는 개별 종목이 아니라 이것을 고르고, 그 안에서
 *  무엇을 살지는 봇이 정한다. devnet 에 미러 토큰이 없는 시장은
 *  tradable=false 이고, note 에 그 이유가 들어 있다. */
export type Market = {
  key: string
  name: string
  country: string
  flag: string
  desc: string
  tickers: string[]
  tradable: boolean
  note: string
}

export type Meta = {
  tags: string[]
  styles: string[]
  goals: string[]
  risks: string[]
  markets: Market[]
  sessions: string[]
  currencies: string[]
  models: string[]
  universe: { ticker: string; company: string }[]
  universe_count: number
  limits: Record<string, number>
  defaults: Record<string, number>
  fx: Fx
}

export type Profile = {
  bot_id: string
  display_name: string
  tagline: string
  prompt: string
  tags: string[]
  style: string
  markets: string[]
  session: string
  base_currency: string
  goal: string
  risk: string
  notify: boolean
  auto_reinvest: boolean
  grant_more_authority: boolean
  model: string
}

export type DeletePreflight = BotHeader & {
  open_positions: {
    ticker: string
    qty: number
    basis_micro: number
    pnl_pct: number
  }[]
  will_close_first: number
  balances: Record<string, number>
  remaining_micro: number
  remaining_krw: number
  fills: number
  withdrawal_supported: boolean
  note: string
}

export type ProfileBundle = {
  profile: Profile
  badge: string
  ai: BotAi
  /** profile.markets 는 키 목록이다. 화면에 쓸 이름은 서버가 붙여준다. */
  market_names: string[]
  rulebook: {
    tickers: string[]
    min_confidence: number
    max_position_usd: number
    max_trades_per_day: number
    take_profit_pct: number
    stop_loss_pct: number
    max_hold_hours: number
  }
  killed: boolean
  orphaned_positions?: string[]
}

/** 매도·정산 한 건. 손익은 진입가와 청산가의 차이에서 나온다. */
export type SoldRow = {
  ticker: string
  company: string
  flag: string
  qty: number
  entry_price: number
  exit_price: number
  held_hours: number
  realized_micro: number
  realized_krw: number
  /** 이익일 때만 채워진다 — 손실이면 분배가 없다. */
  distribution: { to: string; amount: number }[] | null
  tx: string
  explorer: string | null
}

export type SellResult = {
  bot_id: string
  mode: 'all' | 'rulebook'
  open_before: number
  open_after: number
  closed: number
  sold: SoldRow[]
  realized_micro: number
  realized_krw: number
  balances: Record<string, number>
  /** 룰북 모드에서 조건에 안 걸려 그대로 둔 포지션 — 왜 안 팔렸는지. */
  checked: Record<string, unknown>[] | null
  note?: string
}
