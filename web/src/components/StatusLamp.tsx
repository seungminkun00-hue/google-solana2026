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
 * [왜 버튼이 없나 — 2026-08-03]
 * 처음에는 여기에 [시작/정지] 와 [단기 매매 ON/OFF] 를 달았다. 그런데
 * 표시등에 조작부가 붙으니 무엇을 보는 곳인지 무엇을 누르는 곳인지가
 * 흐려졌다. 표시등은 **읽는 것**이다.
 *
 * 조작은 각자 제자리에 있다:
 *   · 자동매매 기동   서버가 뜰 때 자동 (main._autostart_scheduler)
 *   · 봇 하나 멈추기  앱 요약 탭의 일시정지
 *   · 전체 정지       POST /scheduler/stop
 *   · 단기 매매 모드  POST /ui/runtime/scalp  (라우트는 그대로 살아 있다)
 *
 * 폴링 주기는 3초. 카운트다운이 뚝뚝 끊기지 않을 만큼 자주면서,
 * 서버에 부담이 안 될 만큼은 드물다.
 */
export function StatusLamp() {
  const { data } = useApi(() => api.runtime(), [], 3000)

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
          {/* 한 줄에 들어가야 한다. 줄바꿈이 생기면 막대가 두 배로
              두꺼워져서 기기 위에 얹힌 표시등처럼 안 보인다. */}
          {on ? (
            <>
              봇 {data.active_bots} · {data.interval_seconds}초 주기
              {data.next_tick_in !== null && ` · 다음 ${data.next_tick_in}초`}
              {data.ticks > 0 && ` · ${data.ticks}회`}
              {data.scalp && ' · 단기'}
            </>
          ) : idle ? (
            <>정지된 봇 {data.paused_bots}개 — 앱에서 재개할 수 있습니다</>
          ) : (
            <>봇 {data.bots}개 대기 중</>
          )}
        </span>
      </div>
    </div>
  )
}
