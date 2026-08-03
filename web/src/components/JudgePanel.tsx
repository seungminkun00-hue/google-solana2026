import { useCallback, useEffect, useRef, useState } from 'react'
import { api, useApi } from '../api/client'
import { useActivity } from './activityLog'
import { judgeApi, type JudgeStatus } from '../api/judge'
import {
  connect,
  getProvider,
  PHANTOM_INSTALL_URL,
  signAndSend,
} from '../lib/phantom'
import s from './JudgePanel.module.css'

/**
 * 아이폰 목업 오른쪽에 서는 시연 패널.
 *
 * [무엇을 보여주려는 화면인가]
 * 앱 안에서는 봇이 이미 알아서 돈을 쓴다. 그런데 그건 우리가 만든
 * 지갑끼리의 일이라 보는 사람에게는 숫자놀음처럼 보인다. 심사위원
 * **본인의** 지갑에서 코인이 빠져나가야 비로소 전달된다.
 *
 * [순서]
 *   1 지갑 연결 → 2 테스트 코인 → 3 위임 서명(한 번) → 4 매수(여러 번)
 *
 * 3과 4 사이가 이 프로젝트의 주장이다. 서명은 3에서 한 번뿐이고,
 * 4는 몇 번을 눌러도 에이전트가 단독으로 집행한다. 자동이체 신청서를
 * 한 번 쓰면 그 뒤로 매달 알아서 빠져나가는 것과 같다.
 */

const USDC = 1_000_000

function usd(micro: number | undefined): string {
  if (micro === undefined) return '—'
  return `$${(micro / USDC).toFixed(2)}`
}

function short(addr: string | null | undefined): string {
  if (!addr) return '—'
  return `${addr.slice(0, 4)}…${addr.slice(-4)}`
}

type Phase = 'idle' | 'busy'

export function JudgePanel() {
  const [status, setStatus] = useState<JudgeStatus | null>(null)
  // 브라우저에서 팬텀이 연결됐는가. 서버의 '등록됨' 과 구분해야 한다 —
  // 등록은 주소만 있으면 되지만 서명은 연결된 팬텀이 있어야 한다.
  // (서버에 이미 등록된 상태로 화면을 열면 '연결됨' 으로 보이는데
  //  실제로는 서명이 안 되는 상황이 나온다.)
  const [walletAddr, setWalletAddr] = useState<string | null>(
    () => getProvider()?.publicKey?.toString() ?? null,
  )
  const [botId, setBotId] = useState<string>('')
  const [allowance, setAllowance] = useState(20)
  const [draw, setDraw] = useState(5)
  const [depositAmt, setDepositAmt] = useState(50)
  const [phase, setPhase] = useState<Phase>('idle')
  const [note, setNote] = useState<string | null>(null)

  const [addrInput, setAddrInput] = useState('')
  const activity = useActivity()
  const stopRef = useRef<(() => void) | null>(null)

  // 팬텀은 확장프로그램이라 페이지 스크립트보다 늦게 주입될 수 있다.
  // 첫 렌더에 없다고 '설치 안 됨' 으로 굳히면, 실제로는 깔려 있는데
  // 연결 버튼 자체가 안 나온다. 잠깐 동안 다시 확인한다.
  const [hasPhantom, setHasPhantom] = useState(() => getProvider() !== null)
  useEffect(() => {
    if (hasPhantom) return
    const t = setInterval(() => {
      if (getProvider()) {
        setHasPhantom(true)
        clearInterval(t)
      }
    }, 300)
    const give_up = setTimeout(() => clearInterval(t), 5000)
    return () => {
      clearInterval(t)
      clearTimeout(give_up)
    }
  }, [hasPhantom])

  // ⚠️ 반드시 폴링이어야 한다. 이 패널은 페이지가 열릴 때 함께 마운트되는데,
  //    그때는 봇이 하나도 없다(심사위원이 아직 안 만들었으니까). 한 번만
  //    조회하면 봇 선택 목록이 영영 비어 있고, 봇을 만들고 돌아와도
  //    3·4단계가 눌리지 않는다 — 실제로 그 상태로 막혔다.
  const { data: overview } = useApi(() => api.overview(), [], 4_000)

  // 기본 봇: 살아있는 것 우선. 죽은 봇을 기본값으로 잡으면 매수가
  // 킬스위치에 막혀 시연 첫 클릭이 실패한다.
  // 고른 봇이 사라졌으면(삭제) 다시 고른다 — 없는 봇을 가리킨 채로 두면
  // 위임과 매수가 404 로 실패한다.
  useEffect(() => {
    const bots = overview?.bots
    if (!bots?.length) return
    if (botId && bots.some((b) => b.bot_id === botId)) return
    const alive = bots.find((b) => !b.killed)
    setBotId((alive ?? bots[0]).bot_id)
  }, [overview, botId])

  const refresh = useCallback(async () => {
    try {
      setStatus(await judgeApi.status())
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  // 스트림이 열린 채로 화면을 떠나면 백엔드 제너레이터가 남는다.
  useEffect(() => () => stopRef.current?.(), [])

  async function guard(fn: () => Promise<void>) {
    setPhase('busy')
    setNote(null)
    try {
      await fn()
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e))
    } finally {
      setPhase('idle')
      refresh()
    }
  }

  const onConnect = () =>
    guard(async () => {
      const addr = await connect()
      setWalletAddr(addr)
      setAddrInput(addr)
      setStatus(await judgeApi.register(addr))
    })

  /** 주소만 손으로 넣는 경로. 팬텀 없이도 잔고 확인과 테스트 코인
   *  지급까지는 된다 — 넣어주는 건 주소만 알면 되기 때문이다.
   *  인출(위임)만 서명이 필요하고, 그건 팬텀이 있어야 한다. */
  const onRegisterManual = () =>
    guard(async () => {
      const addr = addrInput.trim()
      if (!addr) throw new Error('지갑 주소를 입력하세요')
      setStatus(await judgeApi.register(addr))
      setNote('등록됐습니다. 인출 권한을 주려면 팬텀으로 연결해 서명해야 합니다.')
    })

  /** 매매 없이 자금만 옮긴다. 위임 한도 안에서 서명이 필요 없다. */
  const onDeposit = () =>
    guard(async () => {
      if (!botId) throw new Error('봇을 선택하세요')
      const name =
        overview?.bots?.find((b) => b.bot_id === botId)?.name ?? botId
      activity.begin('judge', `지갑 → ${name} 입금 $${depositAmt}`)
      const r = await judgeApi.deposit(botId, depositAmt * USDC)
      activity.push('judge', [{
        step: 'pull-done',
        amount_usd: r.amount_usd,
        bot_treasury_usd: (r.bot_treasury / USDC).toFixed(2),
        allowance_left_usd: (r.allowance_left / USDC).toFixed(2),
        tx: r.tx,
        explorer: r.explorer,
      }])
      setNote(
        `${name} 에 ${usd(depositAmt * USDC)} 입금됨 · 남은 한도 ` +
          `${usd(r.allowance_left)} — 추가 서명 없음`,
      )
    })

  const onFaucet = () =>
    guard(async () => {
      const r = await judgeApi.faucet()
      setNote(`테스트 USDC ${usd(r.amount)} 지급됨 · ${short(r.tx)}`)
    })

  const onApprove = () =>
    guard(async () => {
      if (!botId) throw new Error('봇을 선택하세요')
      // 연결이 안 돼 있으면 여기서 같이 한다. 버튼을 비활성으로 막아두면
      // '왜 안 눌리는지' 를 화면이 설명하지 못해 막다른 길이 된다.
      if (!walletAddr) {
        const addr = await connect()
        setWalletAddr(addr)
        setAddrInput(addr)
        setStatus(await judgeApi.register(addr))
      }
      const { transaction } = await judgeApi.approveTx(botId, allowance * USDC)
      const sig = await signAndSend(transaction)
      setNote(`위임 완료 · ${short(sig)} — 이제 추가 서명 없이 결제됩니다`)
    })

  const onBuy = () => {
    if (!botId) {
      setNote('봇을 선택하세요')
      return
    }
    setNote(null)
    setPhase('busy')
    activity.setBusy(true)
    const name =
      overview?.bots?.find((b) => b.bot_id === botId)?.name ?? botId
    activity.begin('judge', `지갑 시연 매수 — ${name} · $${draw}`)
    stopRef.current = judgeApi.buy(
      botId,
      draw * USDC,
      // 서버가 보내는 단계를 그대로 오른쪽 로그로 넘긴다. 여기서
      // 재가공하면 화면과 실제 동작이 어긋나기 시작한다.
      (e) => activity.push('judge', [e as unknown as Record<string, unknown>]),
      () => {
        setPhase('idle')
        activity.setBusy(false)
        stopRef.current = null
        refresh()
      },
    )
  }

  /** 매도·정산. 매수와 같은 로그로 흘려보낸다 — 한 바퀴가 이어져 보여야 한다. */
  const onSell = () => {
    if (!botId) {
      setNote('봇을 선택하세요')
      return
    }
    setNote(null)
    setPhase('busy')
    activity.setBusy(true)
    const name = overview?.bots?.find((b) => b.bot_id === botId)?.name ?? botId
    activity.begin('judge', `지갑 시연 매도·정산 — ${name}`)
    stopRef.current = judgeApi.sell(
      botId,
      (e) => activity.push('judge', [e as unknown as Record<string, unknown>]),
      () => {
        setPhase('idle')
        activity.setBusy(false)
        stopRef.current = null
        refresh()
      },
    )
  }

  const busy = phase === 'busy'
  const registered = status?.registered === true
  const delegated = (status?.allowance ?? 0) > 0

  const controls = (
    <aside className={s.panel}>
      <header className={s.head}>
        <h1 className={s.title}>심사위원 시연</h1>
        <p className={s.sub}>
          Solana devnet · 실제 온체인 트랜잭션입니다 (테스트 코인)
        </p>
      </header>

      {/* ① 지갑 연결 */}
      <section className={s.step}>
        <div className={s.stepHead}>
          <span className={s.num}>1</span>
          <h2 className={s.stepTitle}>지갑 연결</h2>
        </div>
        <button
          className={`${s.btn} ${s.primary}`}
          onClick={onConnect}
          disabled={busy || !hasPhantom || walletAddr !== null}
        >
          {walletAddr ? '연결됨' : '팬텀 지갑 연결'}
        </button>

        {!hasPhantom && (
          <p className={s.warn}>
            팬텀이 감지되지 않았습니다.{' '}
            <a href={PHANTOM_INSTALL_URL} target="_blank" rel="noreferrer">
              설치하기
            </a>
          </p>
        )}

        {/* 주소 직접 입력 — 팬텀이 없거나 연결이 안 될 때의 우회로.
            여기까지만으로도 잔고 확인과 테스트 코인 수령은 된다. */}
        <div className={s.row}>
          <label className={s.field}>
            <span>또는 지갑 주소 직접 입력</span>
            <input
              value={addrInput}
              onChange={(e) => setAddrInput(e.target.value)}
              placeholder="예: HMeit…2nP3"
              disabled={busy}
              spellCheck={false}
            />
          </label>
          <button
            className={s.btnSmall}
            onClick={onRegisterManual}
            disabled={busy || !addrInput.trim()}
          >
            등록
          </button>
        </div>

        <p className={s.hint}>
          팬텀을 <b>devnet</b> 으로 바꿔주세요 (설정 → 개발자 설정 → 테스트넷
          모드). 주소만 등록하면 잔고 확인과 테스트 코인 수령까지 되고,
          <b> 인출 권한을 주려면(3번) 팬텀 연결이 필요합니다</b> — 서명해야
          하니까요.
        </p>

        {registered && (
          <dl className={s.kv}>
            <dt>주소</dt>
            <dd className="tnum">{short(status?.address)}</dd>
            <dt>잔고</dt>
            <dd className="tnum">{usd(status?.balance)}</dd>
          </dl>
        )}
      </section>

      {/* ② 테스트 코인 */}
      <section className={s.step} data-off={!registered}>
        <div className={s.stepHead}>
          <span className={s.num}>2</span>
          <h2 className={s.stepTitle}>테스트 USDC 받기</h2>
        </div>
        <button className={s.btn} onClick={onFaucet} disabled={busy || !registered}>
          $50 받기
        </button>
        <p className={s.hint}>
          새로 찍지 않고 시스템 지갑에서 이체합니다 — 통화량 보존 검증을
          깨지 않기 위해서입니다.
        </p>
      </section>

      {/* ③ 위임 */}
      <section className={s.step} data-off={!registered}>
        <div className={s.stepHead}>
          <span className={s.num}>3</span>
          <h2 className={s.stepTitle}>자동결제 위임 — 서명 1회</h2>
        </div>
        <div className={s.row}>
          <label className={s.field}>
            <span>봇</span>
            <select
              value={botId}
              onChange={(e) => setBotId(e.target.value)}
              disabled={busy || !overview?.bots?.length}
            >
              {/* 봇이 없을 때 빈 목록을 두면 왜 못 고르는지 알 수 없다.
                  무엇을 먼저 해야 하는지 그 자리에 적는다. */}
              {!overview?.bots?.length && (
                <option value="">앱에서 봇을 먼저 만들어주세요</option>
              )}
              {overview?.bots?.map((b) => (
                <option key={b.bot_id} value={b.bot_id}>
                  {b.name}
                  {b.killed ? ' (정지됨)' : ''}
                </option>
              ))}
            </select>
          </label>
          <label className={s.field}>
            <span>한도 ($)</span>
            <input
              type="number"
              min={1}
              value={allowance}
              onChange={(e) => setAllowance(Number(e.target.value))}
              disabled={busy}
              className="tnum"
            />
          </label>
        </div>
        {/* 팬텀이 띄울 경고를 버튼 **위**에 미리 적어 둔다.
            이 경고는 버그가 아니라 우리가 실제로 하는 일의 정확한 묘사다.
            팬텀은 '토큰 인출 권한 위임' 을 위험 신호로 표시하도록 만들어져
            있고, 이 데모의 요점이 바로 그 위임이다. 보고 나서 설명하면
            변명처럼 들리므로, 누르기 전에 읽게 한다. */}
        <div className={s.expect}>
          <b className={s.expectTitle}>팬텀이 경고를 띄웁니다 — 정상입니다</b>
          다음 화면에서 이렇게 나옵니다:
          <ul className={s.expectList}>
            <li>“이 dApp은 악성일 수 있습니다”</li>
            <li>“이 도메인은 새 도메인입니다”</li>
          </ul>
          <p className={s.expectWhy}>
            팬텀은 <b>토큰 인출 권한을 넘기는 트랜잭션</b>을 전부 이렇게
            표시합니다. 그리고 이 데모의 핵심이 정확히 그것입니다 — 여기서
            한 번 위임하면, 이후 AI 에이전트가 <b>사람 서명 없이</b> 결제합니다.
            경고가 뜬다는 건 위임이 제대로 걸리고 있다는 뜻입니다.
            한도(위 ${allowance})를 넘는 인출은 체인이 거부하므로 백지수표가
            아닙니다. devnet 테스트 코인이라 실제 자산 위험도 없습니다.
          </p>
        </div>
        {/* 지갑이 아예 없을 때만 막는다. 연결이 안 된 상태면 onApprove 가
            연결부터 하므로 여기서 미리 막을 이유가 없다. */}
        <button
          className={s.btn}
          onClick={onApprove}
          disabled={busy || !hasPhantom || !botId}
        >
          {walletAddr ? '위임 서명' : '연결하고 위임 서명'}
        </button>
        {!hasPhantom && (
          <p className={s.warn}>
            지갑 확장프로그램이 감지되지 않아 서명할 수 없습니다.
          </p>
        )}
        {!overview?.bots?.length && (
          <p className={s.warn}>
            위임할 대상이 없습니다 — 왼쪽 앱에서 봇을 먼저 만들어주세요.
          </p>
        )}
        <dl className={s.kv}>
          <dt>현재 위임</dt>
          <dd className="tnum">
            {delegated ? `${usd(status?.allowance)} → ${short(status?.delegate)}` : '없음'}
          </dd>
        </dl>
        <p className={s.hint}>
          자동이체 신청서와 같습니다. 여기서 한 번 서명하면 아래 매수는
          <b> 추가 서명 없이</b> 집행됩니다. 한도를 넘는 인출은 체인이 거부합니다.
        </p>
      </section>

      {/* ③-b 입금 — 매매 없이 봇에 자금만 더 넣는다 */}
      <section className={s.step} data-off={!delegated}>
        <div className={s.stepHead}>
          <span className={s.num}>+</span>
          <h2 className={s.stepTitle}>이 봇에 입금 — 서명 없음</h2>
        </div>
        <div className={s.row}>
          <label className={s.field}>
            <span>입금액 ($)</span>
            <input
              type="number"
              min={1}
              value={depositAmt}
              onChange={(e) => setDepositAmt(Number(e.target.value))}
              disabled={busy}
              className="tnum"
            />
          </label>
          <button
            className={s.btnSmall}
            onClick={onDeposit}
            disabled={busy || !delegated || !botId}
          >
            입금
          </button>
        </div>
        <dl className={s.kv}>
          <dt>남은 위임 한도</dt>
          <dd className="tnum">{usd(status?.allowance)}</dd>
        </dl>
        <p className={s.hint}>
          위 ③ 에서 고른 봇의 지갑으로 바로 들어갑니다. 매매는 하지 않습니다 —
          <b> 자금만 옮기는 것도 추가 서명 없이</b> 된다는 것을 보여주는 자리입니다.
          한도를 넘기면 체인이 거부합니다(백지수표가 아니라는 증거).
        </p>
      </section>

      {/* ④ 매수 */}
      <section className={s.step} data-off={!delegated}>
        <div className={s.stepHead}>
          <span className={s.num}>4</span>
          <h2 className={s.stepTitle}>매수 — 사람 개입 없음</h2>
        </div>
        <label className={s.field}>
          <span>1회 인출액 ($)</span>
          <input
            type="number"
            min={1}
            value={draw}
            onChange={(e) => setDraw(Number(e.target.value))}
            disabled={busy}
            className="tnum"
          />
        </label>
        <button
          className={`${s.btn} ${s.primary}`}
          onClick={onBuy}
          disabled={busy || !delegated}
        >
          {busy ? '집행 중…' : '매수 실행'}
        </button>
        {/* 앱의 [지금 일해보기] 와 무엇이 다른지. 둘 다 같은 봇 사이클을
            돌리지만, 이쪽은 그 앞에 '심사위원 지갑에서 인출' 이 붙는다. */}
        <p className={s.hint}>
          앱의 <b>[지금 일해보기]</b> 와 같은 사이클을 돌립니다. 다른 점은
          앞에 <b>심사위원 지갑에서 ${draw} 인출</b>이 붙는다는 것 —
          그 인출에 서명이 필요 없다는 게 이 화면의 요점입니다. 노출 한도에
          걸리면 기존 포지션을 먼저 청산하고, 체결까지 최대 3회 재시도합니다.
        </p>
      </section>

      {/* ⑤ 매도·정산 — 돈이 돌아오는 쪽 */}
      <section className={s.step} data-off={!registered}>
        <div className={s.stepHead}>
          <span className={s.num}>5</span>
          <h2 className={s.stepTitle}>매도·정산 — 지갑으로 회수</h2>
        </div>
        <button
          className={`${s.btn} ${s.primary}`}
          onClick={onSell}
          disabled={busy || !registered || !botId}
        >
          {busy ? '정산 중…' : '전량 매도하고 회수'}
        </button>
        <dl className={s.kv}>
          <dt>내 지갑 잔고</dt>
          <dd className="tnum">{usd(status?.balance)}</dd>
        </dl>
        <p className={s.hint}>
          열린 포지션을 전부 청산하고, 정산된 돈을 <b>심사위원 지갑으로
          돌려보냅니다.</b> 청산가는 매수 때와 같은 실시세(한국투자증권
          OpenAPI)에서 오고, 이익이 나면 매수 시점 영수증에 박제된 비율
          (사용자 85 / 판매수익 10 / 인지비용 5)로 나뉩니다.
          위 잔고와 팬텀 지갑에서 실제로 늘어난 것을 확인하세요.
        </p>
      </section>

      {note && <p className={s.note}>{note}</p>}
    </aside>
  )


  // 로그는 오른쪽 공용 기둥(ActivityPanel)이 그린다. 예전에는 이 패널이
  // 자기 로그를 따로 들고 있어서, 다른 탭을 보고 있으면 봇이 무엇을
  // 하는지 아무것도 안 보였다.
  return controls
}
