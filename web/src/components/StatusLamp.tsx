import { useCallback, useState } from 'react'
import { api, useApi } from '../api/client'
import s from './StatusLamp.module.css'

/**
 * 아이폰 목업 **위**에 얹히는 실행 상태 램프.
 *
 * [왜 목업 안이 아니라 위인가]
 * 이건 앱 화면의 일부가 아니라 '이 봇이 지금 살아 있는가' 라는 시연
 * 바깥의 사실이다. 앱 안에 넣으면 아이폰 스크린샷의 일부처럼 보여서
 * 오히려 신뢰가 떨어진다. 기기 위에 붙은 표시등이라야 "실물 장비가
 * 돌고 있다" 는 인상이 된다.
 *
 * [왜 필요한가]
 * 자동매매의 값어치는 '사람이 안 보는 동안에도 돈다' 는 것인데, 화면
 * 어디에도 그게 돌고 있다는 표시가 없었다. 보이지 않는 기능은 시연에서
 * 존재하지 않는 기능이다.
 *
 * 폴링 주기는 3초. 카운트다운이 뚝뚝 끊기지 않을 만큼 자주면서,
 * 서버에 부담이 안 될 만큼은 드물다.
 */
export function StatusLamp() {
  const { data, reload } = useApi(() => api.runtime(), [], 3000)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const act = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true)
      setErr(null)
      try {
        await fn()
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e))
      } finally {
        setBusy(false)
        reload()
      }
    },
    [reload],
  )

  if (!data) return null

  const on = data.running
  // 스케줄러는 도는데 봇이 전부 정지된 상태. 이건 '켜짐' 도 '꺼짐' 도
  // 아니라서 따로 말해줘야 한다 — 안 그러면 "켰는데 왜 아무 일도
  // 안 일어나지?" 가 된다.
  const idle = data.scheduler_running && data.active_bots === 0

  const state = on ? '자동매매 작동 중' : idle ? '봇이 전부 정지됨' : '자동매매 꺼짐'

  return (
    <div className={s.bar} data-on={on} data-idle={idle}>
      <span className={s.lamp} aria-hidden />
      <div className={s.text}>
        <b className={s.state}>{state}</b>
        <span className={s.detail}>
          {on ? (
            // 한 줄에 들어가야 한다. 줄바꿈이 생기면 막대가 두 배로
            // 두꺼워져서 기기 위에 얹힌 표시등처럼 안 보인다.
            <>
              봇 {data.active_bots} · {data.interval_seconds}초 주기
              {data.next_tick_in !== null && ` · 다음 ${data.next_tick_in}초`}
              {data.ticks > 0 && ` · ${data.ticks}회`}
            </>
          ) : idle ? (
            <>정지된 봇 {data.paused_bots}개 — 켜면 함께 재개됩니다</>
          ) : (
            <>봇 {data.bots}개 대기 중</>
          )}
        </span>
      </div>

      {/* 단기 매매 모드. 켜면 익절·손절이 얕아져 매매가 자주 일어난다. */}
      <button
        className={s.toggle}
        data-active={data.scalp}
        onClick={() => act(() => api.scalp(!data.scalp))}
        disabled={busy || data.bots === 0}
        title={
          data.scalp
            ? '기본 설정으로 되돌립니다 (익절 5% · 손절 3% · 보유 24시간)'
            : '익절 0.4% · 손절 0.3% · 보유 3분 — 시연용 설정입니다. ' +
              '매매가 자주 일어나지만 좋은 전략은 아닙니다'
        }
      >
        단기 매매 {data.scalp ? 'ON' : 'OFF'}
      </button>

      <button
        className={s.power}
        data-on={data.scheduler_running}
        onClick={() =>
          act(() =>
            api.scheduler(
              !data.scheduler_running,
              data.scalp ? 45 : undefined,
            ),
          )
        }
        disabled={busy || data.bots === 0}
      >
        {data.scheduler_running ? '정지' : '시작'}
      </button>

      {err && <span className={s.err}>{err}</span>}
    </div>
  )
}
