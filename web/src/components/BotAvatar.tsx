import sparkle from '../assets/icons/gemini-sparkle.png'
import s from './BotAvatar.module.css'

/**
 * 봇 아바타 (Figma node 1:436).
 *
 * 안쪽 별은 Figma에서 래스터 이미지 fill 이었다(벡터가 비어 있음).
 * 그래서 SVG가 아니라 내보낸 PNG를 그대로 쓴다 — 손으로 다시 그리면
 * 다른 그림이 된다.
 */
export function BotAvatar({
  size = 47,
  className,
}: {
  size?: number
  className?: string
}) {
  return (
    <span
      className={`${s.ring} ${className ?? ''}`}
      style={{ width: size, height: size }}
    >
      <img
        className={s.star}
        src={sparkle}
        alt=""
        style={{ width: size * 0.66, height: size * 0.66 }}
      />
    </span>
  )
}
