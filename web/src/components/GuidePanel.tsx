import { useMemo } from 'react'
import { api, useApi } from '../api/client'
import { judgeApi } from '../api/judge'
import { useTour } from './tour'
import s from './GuidePanel.module.css'

/**
 * 아이폰 목업 옆에 서는 진행 안내.
 *
 * [무엇을 하려는 화면인가]
 * 심사위원은 이 웹페이지 하나만 보고 판단한다. 옆에서 설명해 줄 사람이
 * 없으니 화면이 대신해야 한다 — 지금 무엇을 누르면 되는지, 그걸 누르면
 * 무슨 일이 일어나는지, 그게 왜 이 프로젝트의 주장인지.
 *
 * [단계는 어떻게 정해지나]
 * 순서를 세어서 정하지 않는다. 각 단계에 '끝났다고 볼 조건'을 붙이고,
 * 아직 안 끝난 첫 단계를 현재 단계로 본다. 그래서 심사위원이 순서를
 * 건너뛰거나 뒤로 돌아가도 안내가 어긋나지 않는다.
 *
 * 조건의 재료는 전부 실제 상태다 — 봇이 있는가(서버), 체결이 있는가(서버),
 * 어느 화면을 지나왔는가(라우터). 안내가 "했다"고 말하면 진짜로 한 것이다.
 */
type Step = {
  key: string
  title: string
  /** 무엇을 누르면 되는가 */
  action: string
  /** 그게 왜 의미 있는가 — 심사위원에게 하는 말 */
  why: string
  done: boolean
  /** 이 단계를 하려면 옆 「심사위원 지갑 시연」 탭으로 가야 하는가 */
  judge?: boolean
}

export function GuidePanel({ onOpenJudge }: { onOpenJudge?: () => void }) {
  const { booted, visited, ran } = useTour()
  const { data } = useApi(() => api.overview(), [], 5_000)
  // 지갑 연결·위임 여부는 서버가 온체인에서 확인해 준다. 브라우저의
  // 팬텀 상태만 보면 '연결했다고 생각했는데 등록은 안 된' 경우를 놓친다.
  const { data: judge } = useApi(() => judgeApi.status(), [], 5_000)

  const bots = data?.bots ?? []
  const hasBot = bots.length > 0
  // 한 번이라도 매수가 있었는가. 투입원가가 잡혔거나 포지션이 열려 있으면
  // 체결이 있었다는 뜻이다 — 둘 다 서버가 계산한 값이다.
  const traded =
    ran || bots.some((b) => b.return_pct !== null || b.open_positions > 0)
  const walletReady = judge?.registered === true
  const delegated = (judge?.allowance ?? 0) > 0

  const steps: Step[] = useMemo(
    () => [
      {
        key: 'boot',
        title: '앱을 켭니다',
        action: '아이폰 홈 화면에서 파란 「슈퍼SOL 2.0」 아이콘을 누르세요.',
        why: '은행 앱 안에 들어갔을 때를 가정한 화면입니다. 여기서부터는 실제 서버에 붙어 있습니다.',
        done: booted,
      },
      {
        // 지갑을 먼저 붙인다. 뒤로 미루면 봇을 만들고 나서야 팬텀 설치와
        // devnet 전환을 하게 되는데, 그 사이에 시연 흐름이 끊긴다.
        key: 'wallet',
        title: '지갑을 연결합니다',
        action:
          '위 「심사위원 지갑 시연」 탭 → ① 팬텀 지갑 연결 → ② 테스트 USDC $50 받기.',
        why: '팬텀을 devnet 으로 바꿔주세요(설정 → 개발자 설정). 이 지갑에서 나중에 실제로 돈이 빠져나가는 것을 보게 됩니다. 수수료는 저희가 내므로 devnet SOL 은 없어도 됩니다.',
        done: walletReady,
        judge: true,
      },
      {
        key: 'add',
        title: '봇을 만듭니다',
        action:
          '앱으로 돌아와 「나의 봇」 오른쪽 [+ 추가하기] → 이름 · 프롬프트 · 거래할 시장을 정하고 [봇 만들기].',
        why: '종목은 고르지 않습니다. 시장만 정하면 그 안에서 무엇을 살지는 봇이 뉴스와 추론으로 스스로 고릅니다. 방금 정한 값이 그대로 Gemini 지침이 되고, 만든 직후 그 원문을 보여줍니다.',
        done: hasBot,
      },
      {
        key: 'delegate',
        title: '자동결제를 위임합니다 — 서명 1회',
        action: '「심사위원 지갑 시연」 탭 → ③ 방금 만든 봇을 고르고 [위임 서명].',
        why: '자동이체 신청서와 같습니다. 여기서 한 번 서명하면 이후 결제는 추가 서명 없이 봇이 단독으로 집행합니다. 한도를 넘는 인출은 체인이 거부합니다.',
        done: delegated,
        judge: true,
      },
      {
        key: 'run',
        title: '봇을 일하게 합니다',
        action:
          '앱에서 봇 카드를 열고 요약 탭의 [지금 일해보기]. 또는 지갑 시연 탭의 ④ [매수 실행].',
        why: '뉴스 구매 → 1차 스크리닝 → 심층 추론 → 룰북 검사 → 체결이 실제로 일어납니다. 체결되면 화면 위에 알림이 뜹니다. 15~30초.',
        done: traded,
      },
      {
        key: 'trades',
        title: '거래 내역을 확인합니다',
        action: '봇 상세의 「거래 내역」 탭.',
        why: '각 체결에 Solana devnet 서명이 붙어 있습니다. 눌러서 Explorer 의 수량과 화면의 수량을 대조해 보세요.',
        done: visited.has('trades'),
      },
      {
        key: 'apis',
        title: 'API 결제를 확인합니다',
        action: '「API」 탭 — 최근 결제 내역.',
        why: '호출 한 건마다 결제 한 건, 그리고 온체인 서명 하나가 남습니다. 구독이 아니라 사용량 결제입니다.',
        done: visited.has('apis'),
      },
      {
        key: 'chat',
        title: '봇과 대화합니다',
        action: '요약 탭 아래 [대화하기]. 손익과 판단 이유를 묻고, 주식과 무관한 것도 한번 물어보세요.',
        why: '수치는 원장에서만 가져옵니다. 해석과 의견은 봇이 하고, 주식·투자 밖의 질문은 답하지 않습니다.',
        done: visited.has('chat'),
      },
    ],
    [booted, hasBot, traded, visited, walletReady, delegated],
  )

  const current = steps.findIndex((x) => !x.done)
  const doneCount = steps.filter((x) => x.done).length

  return (
    <aside className={s.panel}>
      <header className={s.head}>
        <h1 className={s.title}>진행 안내</h1>
        <p className={s.sub}>
          왼쪽 화면을 순서대로 눌러보세요. 각 단계는 실제로 동작합니다.
        </p>
        <div className={s.bar} aria-hidden>
          <span style={{ width: `${(doneCount / steps.length) * 100}%` }} />
        </div>
        <p className={s.count}>
          {doneCount} / {steps.length} 단계
        </p>
      </header>

      <ol className={s.steps}>
        {steps.map((st, i) => (
          <li
            key={st.key}
            className={s.step}
            data-state={st.done ? 'done' : i === current ? 'now' : 'todo'}
          >
            <span className={s.num}>{st.done ? '✓' : i + 1}</span>
            <div className={s.body}>
              <h2 className={s.stepTitle}>{st.title}</h2>
              {(i === current || !st.done) && (
                <p className={s.action}>{st.action}</p>
              )}
              <p className={s.why}>{st.why}</p>
              {/* 지갑 쪽 단계는 다른 탭에서 해야 한다. 탭을 찾아 헤매지
                  않도록 그 자리에서 바로 열어준다. */}
              {st.judge && i === current && onOpenJudge && (
                <button type="button" className={s.jump} onClick={onOpenJudge}>
                  지갑 시연 탭 열기 →
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>

      {current === -1 && (
        <p className={s.finished}>
          여기까지가 전체 흐름입니다. 사람이 서명한 것은 3단계의 위임 한 번뿐이고,
          그 뒤의 결제·판단·체결은 전부 봇이 단독으로 집행했습니다.
        </p>
      )}

      {data && (
        <dl className={s.env}>
          <dt>원장</dt>
          <dd>
            {data.ledger_mode === 'devnet'
              ? 'Solana devnet — 실제 온체인'
              : data.ledger_mode}
          </dd>
          <dt>시세</dt>
          <dd>
            {data.price_live
              ? '한국투자증권 OpenAPI 실시세'
              : `내장 기준가 (${data.price_source})`}
          </dd>
          <dt>추론</dt>
          <dd>
            {data.inference_live
              ? 'Gemini 실추론'
              : `모의 판단 (${data.inference_mode})`}
          </dd>
        </dl>
      )}

      <p className={s.foot}>
        표시되는 금액·수익률·승률은 전부 원장과 저널에서 계산된 값입니다.
        목업 숫자는 하나도 없습니다.
      </p>
    </aside>
  )
}
