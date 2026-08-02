// 화면에 찍히는 모든 숫자가 여기를 지난다.
// 서식이 화면마다 다르면 같은 값이 다르게 읽힌다.

const USDC = 1_000_000

/** 12,345,678원 */
export function krw(v: number | null | undefined): string {
  if (v == null) return '—'
  return `${Math.round(v).toLocaleString('ko-KR')}원`
}

/**
 * 12,345,678 — '원' 없이 숫자만.
 *
 * 표 안에서 쓴다. 디자인의 거래내역 표도 단위 없이 숫자만 늘어놓고
 * 헤더에서 통화를 한 번만 밝힌다. 셀마다 '원'을 붙이면 402px 폭에서
 * 마지막 열이 잘린다.
 */
export function krwBare(v: number | null | undefined): string {
  if (v == null) return '—'
  return Math.round(v).toLocaleString('ko-KR')
}

/** 부호를 항상 붙인다. 손익처럼 방향이 뜻을 갖는 값에 쓴다. */
export function krwSigned(v: number | null | undefined): string {
  if (v == null) return '—'
  const n = Math.round(v)
  return `${n > 0 ? '+' : ''}${n.toLocaleString('ko-KR')}원`
}

/** $74,452 — 달러는 소수점을 버린다 (디자인의 총자산 옆 표기). */
export function usd(v: number | null | undefined): string {
  if (v == null) return '—'
  return `$${Math.round(v).toLocaleString('en-US')}`
}

export function usdc(micro: number | null | undefined, digits = 2): string {
  if (micro == null) return '—'
  return `${(micro / USDC).toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })} USDC`
}

/**
 * 1.98%
 *
 * null 은 0%가 아니다. 아직 잴 수 없다는 뜻이라 '—' 로 나간다.
 * 이걸 0으로 뭉개면 "손익 없음"과 "측정 불가"가 같아 보인다.
 */
export function pct(v: number | null | undefined, digits = 2): string {
  if (v == null) return '—'
  return `${v.toFixed(digits)}%`
}

export function pctSigned(v: number | null | undefined, digits = 2): string {
  if (v == null) return '—'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

/** 상승/하락/보합 → 색 토큰 이름 */
export function tone(v: number | null | undefined): 'up' | 'down' | 'flat' {
  if (v == null || v === 0) return 'flat'
  return v > 0 ? 'up' : 'down'
}

/** 0.127611주 — 미러 토큰은 소수점 6자리까지 쪼개진다. */
export function qty(v: number): string {
  const s = v.toFixed(6).replace(/0+$/, '').replace(/\.$/, '')
  return `${s}주`
}

/** 08.01 / 09:23 — 거래 내역 테이블의 2줄 표기 */
export function dateLines(ts: number): [string, string] {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return [
    `${p(d.getMonth() + 1)}.${p(d.getDate())}`,
    `${p(d.getHours())}:${p(d.getMinutes())}`,
  ]
}

/** "방금 전" / "3분 전" / "2시간 전" */
export function ago(ts: number | null): string {
  if (ts == null) return ''
  const s = Date.now() / 1000 - ts
  if (s < 60) return '방금 전'
  if (s < 3600) return `${Math.floor(s / 60)}분 전`
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`
  return `${Math.floor(s / 86400)}일 전`
}

/** 보유 시간 — 1시간 미만이면 분으로 내린다. "2.3회"가 아니라 시간이다. */
export function hours(v: number | null | undefined): string {
  if (v == null) return '—'
  if (v < 1) return `${Math.round(v * 60)}분`
  if (v < 48) return `${v.toFixed(1)}시간`
  return `${(v / 24).toFixed(1)}일`
}

/** 지갑 주소 축약: HMeitxXzmznx7··· */
export function shortAddress(a: string | null, head = 12): string {
  if (!a) return '주소 없음'
  return a.length <= head ? a : `${a.slice(0, head)}···`
}

/** 짧은 기간 라벨 */
export function spanLabel(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}초`
  if (seconds < 3600) return `${Math.round(seconds / 60)}분`
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}시간`
  return `${(seconds / 86400).toFixed(1)}일`
}

export const RANGE_LABELS: Record<string, string> = {
  '1w': '1주',
  '1m': '1개월',
  '3m': '3개월',
  '6m': '6개월',
  all: '전체',
}
