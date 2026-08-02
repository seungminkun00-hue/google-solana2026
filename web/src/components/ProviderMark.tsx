import shinhan from '../assets/shinhan.png'
import toss from '../assets/toss.png'
import { BotAvatar } from './BotAvatar'
import s from './ProviderMark.module.css'

/**
 * 연결된 API 옆 표식.
 *
 * 실제 로고 파일이 있는 공급자는 그 이미지를 그대로 쓴다. 없는 곳만
 * 이름 첫 글자 모노그램으로 세운다 — 남의 상표를 지어낼 수는 없다.
 *
 * [테두리를 그리지 않는 이유]
 * 받은 로고(신한·토스)는 원형 테두리가 **이미지 안에 이미 들어 있다.**
 * 여기서 배경 원을 또 깔면 테두리가 두 겹이 되고, 그러면 원본 디자인과
 * 달라진다. 이미지가 스스로 완결된 표식이므로 그대로 놓는다.
 */
const LOGOS: Record<string, string> = {
  shinhan: shinhan,
  toss: toss,
}

const PALETTE = ['#0046ff', '#6b85c9', '#00b7ff', '#14317d', '#5f89f4', '#8ed0ea']

function hue(key: string): string {
  let h = 0
  for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) >>> 0
  return PALETTE[h % PALETTE.length]
}

function initial(name: string): string {
  const t = name.trim()
  if (!t) return '?'
  // 한글이면 첫 글자, 영문이면 첫 알파벳
  const m = t.match(/[A-Za-z]/)
  return (m ? m[0] : t[0]).toUpperCase()
}

export function ProviderMark({
  providerKey,
  name,
  size = 40,
}: {
  providerKey: string
  name: string
  size?: number
}) {
  const logo = LOGOS[providerKey]
  if (logo) {
    return (
      <img
        className={s.logo}
        src={logo}
        alt=""
        width={size}
        height={size}
        style={{ width: size, height: size }}
      />
    )
  }

  if (providerKey.startsWith('gemini')) {
    return <BotAvatar size={size} />
  }

  return (
    <span
      className={s.mark}
      style={{ width: size, height: size, background: hue(providerKey) }}
      aria-hidden
    >
      <span style={{ fontSize: size * 0.42 }}>{initial(name)}</span>
    </span>
  )
}
