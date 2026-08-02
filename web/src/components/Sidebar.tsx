import { useState } from 'react'
import { ActivityPanel } from './ActivityPanel'
import { GuidePanel } from './GuidePanel'
import { JudgePanel } from './JudgePanel'
import s from './Sidebar.module.css'

/**
 * 목업 옆에 서는 두 패널을 한 자리에서 갈아끼운다.
 *
 * [왜 탭인가]
 * 둘을 나란히 세우면 폰 + 안내(400) + 조작부(400) + 로그(360) 로 1500px
 * 이 넘게 필요하다. 노트북 화면에서는 폰이 알아볼 수 없을 만큼 줄어든다.
 * 한 자리를 나눠 쓰면 지금 폭 그대로 둘 다 들어간다.
 *
 * 기본값은 「진행 안내」다. 처음 여는 사람이 먼저 봐야 할 것이 그쪽이고,
 * 지갑 시연은 팬텀이 필요한 선택 경로이기 때문이다.
 *
 * JudgePanel 은 마운트된 채로 둔다(감추기만 한다). 매수 스트림이 도는
 * 중에 탭을 옮겼다고 언마운트되면 진행 중인 시연이 통째로 끊긴다.
 */
type Tab = 'guide' | 'judge'

export function Sidebar() {
  const [tab, setTab] = useState<Tab>('guide')

  return (
    <div className={s.wrap} data-tab={tab}>
      <div className={s.tabs} role="tablist" aria-label="옆 패널">
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'guide'}
          className={`${s.tab} ${tab === 'guide' ? s.on : ''}`}
          onClick={() => setTab('guide')}
        >
          진행 안내
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === 'judge'}
          className={`${s.tab} ${tab === 'judge' ? s.on : ''}`}
          onClick={() => setTab('judge')}
        >
          심사위원 지갑 시연
        </button>
      </div>

      <div className={s.body}>
        {/* 왼쪽 기둥은 탭에 따라 바뀌고, 오른쪽 로그는 항상 그대로 있다.
            안내를 읽는 동안에도 봇이 무엇을 하는지 보여야 한다. */}
        <div className={tab === 'guide' ? s.show : s.hide}>
          <GuidePanel onOpenJudge={() => setTab('judge')} />
        </div>
        <div className={tab === 'judge' ? s.show : s.hide}>
          <JudgePanel />
        </div>
        <ActivityPanel />
      </div>
    </div>
  )
}
