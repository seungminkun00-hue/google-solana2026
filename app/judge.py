"""심사위원 시연 창구 — 아이폰 목업 오른쪽 패널이 쓰는 라우터.

[이 파일이 존재하는 이유]
앱 화면 안에서는 봇이 이미 '알아서' 돈을 쓴다. 그런데 그건 우리가
만든 지갑끼리의 이야기라, 보는 사람 입장에서는 숫자가 도는 것처럼만
보인다. 심사위원 본인의 지갑에서 실제로 코인이 빠져나가야 비로소
"이게 진짜 돈을 쓰는 에이전트"라는 게 전달된다.

[흐름]
  1. 지갑 등록      POST /judge/register     주소만 받는다. 개인키는 안 받는다.
  2. 테스트 코인    POST /judge/faucet       external 지갑에서 이체 (발행 아님)
  3. 위임 서명 1회  POST /judge/approve-tx   → 브라우저에서 팬텀이 서명
  4. 매수           GET  /judge/buy          SSE 로 전 과정을 흘려보낸다
                                             ★ 여기서 심사위원 서명은 없다

3번과 4번 사이가 이 프로젝트의 주장이다. 사람의 승인은 3번 한 번뿐이고
4번은 몇 번을 눌러도 에이전트가 단독으로 집행한다.

[mock 모드]
LEDGER_MODE=mock 이면 위임이라는 개념 자체가 없다(온체인이 아니므로).
그때는 모든 엔드포인트가 503 으로 정직하게 거절한다. 가짜 시그니처를
만들어 보여주면 시연이 거짓말이 된다.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app import config
from app.core.ledger import LEDGER
from app.core.session import clean, session_id

router = APIRouter(prefix="/judge", tags=["judge"])

# 심사위원 지갑의 논리명.
#
# [2026-08-03] 슬롯 하나였던 것을 **세션마다** 나눴다. 예전에는
# "judge-wallet@demo" 상수 하나라, 심사위원 A 가 팬텀을 연결한 뒤 B 가
# 연결하면 B 가 A 를 덮어썼다 — 같은 링크를 여럿에게 뿌리면 반드시 터진다.
#
# 역할(@ 앞)은 그대로 judge-wallet 이라 경로 규칙
# ("judge-wallet","user-treasury") 이 세션 수와 무관하게 그대로 통한다.
# audit.py 가 검증하는 성질도 건드리지 않는다.
def judge_wallet(session: str) -> str:
    return f"judge-wallet@{session or 'demo'}"

EXPLORER = "https://explorer.solana.com/tx/{}?cluster=devnet"

# 시연 기본값. 위임 한도는 넉넉하되 무한이 아니다 —
# "백지수표가 아니다"가 이 화면이 보여줘야 할 것 중 하나다.
DEFAULT_FAUCET = 50 * config.USDC          # $50
DEFAULT_ALLOWANCE = 20 * config.USDC       # $20
DEFAULT_DRAW = 5 * config.USDC             # 매수 1회당 인출액


# 심사위원 지갑 주소를 남겨두는 곳.
#
# [왜 저장하나]
# 등록은 메모리(LEDGER.external_owners)에만 있었다. 서버를 한 번 재시작하면
# 브라우저의 팬텀은 여전히 연결돼 있는데 서버는 그 지갑을 모르는 상태가 된다 —
# 화면은 "연결됨" 인데 입금·매수가 "지갑을 먼저 등록하세요" 로 실패한다.
#
# 여기 들어가는 것은 **공개키뿐**이다. 비밀은 없다. 개인키는 애초에
# 우리가 가진 적이 없고, 그게 이 시연의 전제다.
STATE_PATH = pathlib.Path("wallets") / "judge_wallets.json"


def _saved() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return {}


def _remember(session: str, address: str) -> None:
    try:
        data = _saved()
        data[session or "demo"] = address
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 심사위원 지갑 주소 저장 실패: {str(e)[:80]}")


async def _restore(session: str) -> bool:
    """재시작으로 잃어버린 등록을 되살린다. 없으면 False."""
    name = judge_wallet(session)
    if name in getattr(LEDGER, "external_owners", {}):
        return True
    addr = _saved().get(session or "demo")
    if not addr:
        return False
    try:
        LEDGER.register_external(name, addr)
        await LEDGER.ensure_external_account(name)
        print(f"  ↺ 심사위원 지갑 복원({name}): {addr}")
        return True
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 심사위원 지갑 복원 실패: {str(e)[:90]}")
        return False


def _ensure_devnet() -> None:
    """위임 인출이 가능한 원장인지 확인한다.

    hasattr 로 보는 이유: mock Ledger 와 DevnetLedger 는 상속 관계가
    아니라 같은 인터페이스를 각자 구현한 관계다. isinstance 로 묶으면
    그 설계를 깨야 한다.
    """
    if not hasattr(LEDGER, "delegated_transfer"):
        raise HTTPException(
            503,
            "이 기능은 devnet 원장에서만 동작합니다. "
            "LEDGER_MODE=devnet 으로 서버를 다시 띄우세요.")


class RegisterBody(BaseModel):
    address: str = Field(min_length=32, max_length=44)


class ApproveBody(BaseModel):
    bot_id: str
    amount: int = Field(default=DEFAULT_ALLOWANCE, gt=0)


@router.post("/register")
async def register(body: RegisterBody,
                   session: str = Depends(session_id)) -> dict:
    """심사위원 지갑 주소를 등록하고 토큰 계정을 준비한다.

    개인키를 요구하지 않는다는 점이 중요하다. 요구하지 않기 때문에
    돈을 빼려면 반드시 위임을 받아야 하고, 그래서 시연이 정직해진다.
    """
    _ensure_devnet()
    wallet = judge_wallet(session)
    try:
        # 형식만 보지 않고 체인에서 '지갑이 맞는지' 확인한다. 민트 주소를
        # 붙여넣으면 여기서 걸린다 — 실제로 그 사고가 났었다.
        await LEDGER.assert_wallet(body.address)
        pubkey = LEDGER.register_external(wallet, body.address)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    await LEDGER.ensure_external_account(wallet)
    # 재시작해도 잃지 않도록 주소를 남긴다(공개키뿐 — 비밀 없음).
    _remember(session, str(pubkey))
    status = await LEDGER.delegate_status(wallet)
    return {"address": str(pubkey), "wallet": wallet, **status}


@router.get("/status")
async def status(session: str = Depends(session_id)) -> dict:
    """잔고와 위임 상태. 패널이 주기적으로 물어본다."""
    _ensure_devnet()
    if not await _restore(session):
        return {"registered": False}
    wallet = judge_wallet(session)
    st = await LEDGER.delegate_status(wallet)
    return {"registered": True,
            "address": str(LEDGER.owner_pubkey(wallet)), **st}


@router.post("/faucet")
async def faucet(amount: int = DEFAULT_FAUCET,
                 session: str = Depends(session_id)) -> dict:
    """시연용 테스트 USDC 지급.

    새로 찍지 않고 external 지갑에서 이체한다. 발행하면 통화량이 늘어
    audit_supply 가 깨진다 — 시연용 코인도 어딘가에서 와야 한다.
    """
    _ensure_devnet()
    if not await _restore(session):
        raise HTTPException(400, "지갑을 먼저 등록하세요")
    proof = await LEDGER.fund_external(judge_wallet(session), amount)
    return {"amount": amount, "tx": proof.proof_id,
            "explorer": EXPLORER.format(proof.proof_id)}


@router.post("/approve-tx")
async def approve_tx(body: ApproveBody,
                     session: str = Depends(session_id)) -> dict:
    """심사위원이 서명할 위임 트랜잭션을 만들어 준다 (부분 서명 상태).

    수수료는 우리가 내므로 심사위원은 devnet SOL 이 없어도 된다.
    서명 요청이 한 번뿐이라는 것이 이 시연의 핵심이다.
    """
    _ensure_devnet()
    if not await _restore(session):
        raise HTTPException(400, "지갑을 먼저 등록하세요")
    delegate = f"user-treasury@{body.bot_id}"
    if delegate not in LEDGER.keypairs:
        raise HTTPException(404, f"봇 지갑 없음: {delegate}")

    tx_b64 = await LEDGER.build_approve_tx(
        judge_wallet(session), delegate, body.amount)
    return {"transaction": tx_b64, "delegate": str(LEDGER.owner_pubkey(delegate)),
            "amount": body.amount, "bot_id": body.bot_id}


class DepositBody(BaseModel):
    bot_id: str
    amount: int = Field(gt=0)


@router.post("/deposit")
async def deposit(body: DepositBody,
                  session: str = Depends(session_id)) -> dict:
    """심사위원 지갑 → 봇 트레저리로 **입금만** 한다. 매매는 하지 않는다.

    [왜 따로 필요한가]
    지금까지 심사위원 지갑에서 돈이 나가는 경로는 '매수 실행'뿐이었다.
    그건 인출과 매매를 한 번에 하므로, "이 봇에 100달러만 더 넣고 싶다"를
    할 방법이 없었다. 위임은 이미 받아둔 것이므로 인출 자체는 서명 없이
    되고, 그 사실을 가장 단순하게 보여주는 것이 이 라우트다.

    한도(allowance)를 넘으면 체인이 거부한다 — 위임이 백지수표가 아니라는
    증거라서, 넘겼을 때 그대로 실패하는 편이 낫다. 다만 왜 실패했는지는
    미리 알려준다.
    """
    _ensure_devnet()
    if not await _restore(session):
        raise HTTPException(400, "지갑을 먼저 등록하세요")

    from app.bots import BOTS
    if body.bot_id not in BOTS:
        raise HTTPException(404, f"봇 없음: {body.bot_id}")

    wallet = judge_wallet(session)
    st = await LEDGER.delegate_status(wallet)
    if st["allowance"] <= 0:
        raise HTTPException(409, {
            "error": "위임이 없습니다",
            "hint": "③ 위임 서명을 먼저 하세요. 한 번 서명하면 이후 인출은 "
                    "추가 서명 없이 됩니다."})
    if body.amount > st["allowance"]:
        raise HTTPException(409, {
            "error": "위임 한도 초과",
            "allowance_usd": round(st["allowance"] / config.USDC, 2),
            "hint": "③ 에서 한도를 더 크게 잡아 다시 서명하세요."})
    if body.amount > st["balance"]:
        raise HTTPException(409, {
            "error": "지갑 잔고 부족",
            "balance_usd": round(st["balance"] / config.USDC, 2),
            "hint": "② 에서 테스트 USDC 를 더 받으세요."})

    treasury = f"user-treasury@{body.bot_id}"
    proof = await LEDGER.delegated_transfer(
        wallet, treasury, body.amount, f"judge-deposit:{body.bot_id}")

    # 앱 상단 팝업. 매수와 같은 자리에 뜬다 — 사용자에게 '돈이 움직였다'는
    # 사건은 매수든 충전이든 똑같이 즉시 보여야 하는 일이다.
    from app.core.events import emit_deposit
    emit_deposit(bot_id=body.bot_id, amount_micro=body.amount,
                 tx=proof.proof_id, source="내 지갑")

    after = await LEDGER.delegate_status(wallet)
    snap = await LEDGER.snapshot()
    return {
        "bot_id": body.bot_id,
        "amount": body.amount,
        "amount_usd": round(body.amount / config.USDC, 2),
        "tx": proof.proof_id,
        "explorer": EXPLORER.format(proof.proof_id),
        "judge_balance": after["balance"],
        "allowance_left": after["allowance"],
        "bot_treasury": snap.get(treasury, 0),
        "note": "심사위원 추가 서명 없음 — 위임 권한으로 집행",
    }


# ── 매수: 전 과정을 실시간으로 흘려보낸다 ───────────────────────────
def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _fill_summary(log: list[dict]) -> dict:
    """사이클 로그에서 '무엇을 얼마에 샀는지' 만 뽑아낸다.

    executor 단계가 곧 체결이다. 없으면 이번엔 안 산 것이고, 그 사실을
    빈 값으로 정직하게 돌려준다 — 지어내면 시연이 거짓말이 된다.
    """
    for s in log:
        if s.get("step") == "executor" and "qty" in s:
            return {"ticker": s.get("ticker"), "qty": s.get("qty"),
                    "entry_price": s.get("entry_price"),
                    "anchor_root": s.get("anchor_root")}
    return {}


def _preflight(bot) -> list[dict]:
    """시연 직전에 봇을 '살 수 있는 상태'로 되돌린다.

    되돌리는 것은 **시연을 반복해서 생긴 상태**뿐이다:
      · trades_today — 두세 번 돌리면 일일 거래 한도에 걸린다
      · spent_today  — 인지비용 일일 상한($0.50)이 몇 번 만에 소진된다
      · killed       — 앞선 시연에서 정지시켰을 수 있다
    셋 다 '하루' 라는 시간 단위에 묶인 값인데, 시연은 여러 날을 몇 분에
    압축해 돌린다. 리셋은 '새 날이 시작됐다' 와 같은 뜻이고, 무엇을
    되돌렸는지 로그에 그대로 띄운다 — 감추면 시연이 거짓말이 된다.

    룰북·만다트·확신도 기준은 건드리지 않는다. 그건 심사위원이 직접
    정한 값이고, 몰래 고치면 시연 전체가 무의미해진다.

    [실측 2026-08-02] spent_today 를 빼먹었더니 6회 반복에서 4회가
    DEFER(예산 부족)로 끝났다. 거래 한도만 리셋하면 안 된다.
    """
    from app.agents import pipeline

    notes: list[dict] = []
    if bot.trades_today:
        notes.append({"reset": "trades_today", "was": bot.trades_today})
        bot.trades_today = 0
    if bot.tracker.spent_today:
        notes.append({"reset": "spent_today", "was": bot.tracker.spent_today})
        bot.tracker.spent_today = 0
        bot.tracker.decision_spent = 0
    if bot.killed:
        notes.append({"reset": "killed", "was": True})
        bot.killed = False

    # 편향이 줄 수 있는 최대치(0.95/0.95)로 미리 물어본다. 이래도 안
    # 통과하면 사이클을 돌려봐야 결과가 같다 — 미리 이유를 말해준다.
    verdict, detail = pipeline.should_think(bot, 0.95, 0.95)
    if verdict == "SKIP":
        notes.append({
            "warn": "이 룰북으로는 봇이 매수까지 가지 않습니다. 기대가치가 "
                    "추론 비용의 5배에 못 미칩니다 — 최대 포지션을 키우세요.",
            "est_value": detail["est_value"],
            "min_required": detail["min_required"]})
    elif verdict == "DEFER":
        notes.append({
            "warn": "인지비용 예산이 부족해 봇이 추론을 미룹니다. "
                    "리셋 직후라면 만다트가 보충할 때까지 기다려야 합니다.",
            "est_cost": detail["est_cost"]})
    return notes


async def _buy_events(bot_id: str, draw: int, session: str,
                      attempts: int = 3):
    """위임 인출 → x402 사이클 을 한 단계씩 내보낸다.

    각 단계를 '보여주기 위해' 만든 것이 아니라, 실제로 일어나는 순서를
    그대로 중계한다. 그래서 실패도 그대로 나간다 — 실패를 감추면
    로그가 시연용 장식이 되고, 그러면 볼 이유가 없다.

    [왜 재시도하는가]
    뉴스를 편향시켜도 결정은 확률적이다(신규성이 범위 안에서 뽑히고,
    만다트가 거절할 수도 있다). 심사위원 앞에서 한 번 미끄러졌다고
    끝나면 안 되니 체결이 나올 때까지 최대 attempts 회 돌린다.
    각 시도는 실제 사이클이라 API 결제도 그만큼 실제로 일어난다.
    """
    t0 = time.monotonic()
    wallet = judge_wallet(session)

    def ev(step: str, **kw) -> str:
        return _sse({"step": step, "t": round(time.monotonic() - t0, 2), **kw})

    try:
        # [1] 위임 확인 — 한도가 남아 있는지 온체인에서 직접 본다
        st = await LEDGER.delegate_status(wallet)
        yield ev("delegate-check", allowance=st["allowance"],
                 balance=st["balance"], delegate=st["delegate"])
        if st["allowance"] < draw:
            yield ev("blocked",
                     reason=f"위임 한도 부족: {st['allowance']} < {draw}. "
                            f"다시 위임하세요.")
            return
        if st["balance"] < draw:
            yield ev("blocked",
                     reason=f"심사위원 지갑 잔고 부족: {st['balance']} < {draw}")
            return

        # [2] 위임 인출 — ★ 심사위원 서명 없이 돈이 빠져나가는 순간
        treasury = f"user-treasury@{bot_id}"
        yield ev("pull-start", frm=str(LEDGER.owner_pubkey(wallet)),
                 to=treasury, amount=draw)
        proof = await LEDGER.delegated_transfer(
            wallet, treasury, draw, "judge-demo-deposit")
        yield ev("pull-done", tx=proof.proof_id,
                 explorer=EXPLORER.format(proof.proof_id),
                 note="심사위원 추가 서명 없음 — 위임 권한으로 집행")

        # [3] 시연 준비 — 반복 시연으로 생긴 상태만 되돌린다
        from app.bots import BOTS
        from app.external import demo_bias
        bot = BOTS[bot_id]
        for note in _preflight(bot):
            yield ev("preflight", **note)

        from app.main import app as fastapi_app, INTERNAL_HEADERS, BASE
        transport = httpx.ASGITransport(app=fastapi_app)

        # [3-b] 노출 한도 정리 — 리셋으로는 못 푸는 유일한 항목
        #
        # 총 노출은 '열린 포지션의 원가 합' 이라 시간을 되돌린다고
        # 줄어들지 않는다. 실제로 팔아야 준다. 시연을 열 번쯤 돌리면
        # $300 상한에 닿아 그 뒤로는 무조건 '총 노출 한도 초과' 로 끝난다.
        # (실측: 12회 반복 후 6/6 전부 이 사유로 거절)
        #
        # 그래서 여기서만 실제 매도를 한다. 감추지 않고 단계로 내보내는
        # 이유는, 이게 상태 리셋이 아니라 돈이 움직이는 행위이기 때문이다.
        # 부수 효과로 청산·정산 경로가 심사위원에게 한 번 더 보인다.
        from app.core.positions import BOOK
        exposure = BOOK.exposure(bot_id)
        if exposure + draw > config.CAP_MANDATE_MAX_EXPOSURE:
            yield ev("close-open", exposure=exposure,
                     cap=config.CAP_MANDATE_MAX_EXPOSURE,
                     note="총 노출이 한도에 닿아 기존 포지션을 청산합니다")
            async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                         headers=INTERNAL_HEADERS,
                                         timeout=120.0) as client:
                cl = await client.post(f"{BASE}/bots/{bot_id}/close-all")
            yield ev("close-done",
                     closed=cl.json().get("closed") if cl.status_code == 200 else None,
                     exposure_after=BOOK.exposure(bot_id))

        # [4] 봇 사이클 — x402 결제·시그널·테제·룰북·체결이 전부 여기서
        filled = False
        body: dict = {}

        for attempt in range(1, attempts + 1):
            yield ev("cycle-start", bot_id=bot_id, attempt=attempt,
                     of=attempts)
            # 편향은 이 블록 안에서만 걸린다. 스케줄러가 돌리는 다른 봇의
            # 사이클에는 영향이 없다.
            with demo_bias(bot.rulebook.allowed_tickers):
                async with httpx.AsyncClient(transport=transport,
                                             base_url=BASE,
                                             headers=INTERNAL_HEADERS,
                                             timeout=180.0) as client:
                    res = await client.post(f"{BASE}/bots/{bot_id}/cycle")
            if res.status_code != 200:
                yield ev("blocked", reason=f"사이클 실패 {res.status_code}: "
                                           f"{res.text[:160]}")
                return

            body = res.json()
            # 사이클이 남긴 단계 로그를 그대로 중계한다. 여기서 재가공하면
            # 화면과 실제 동작이 어긋나기 시작한다.
            for step in body.get("log", []):
                yield ev(f"cycle:{step.get('step', '?')}",
                         **{k: v for k, v in step.items() if k != "step"})
                await asyncio.sleep(0.15)   # 사람이 읽을 수 있는 속도로

            filled = any(s.get("step") == "executor" and "qty" in s
                         for s in body.get("log", []))
            if filled:
                break
            if attempt < attempts:
                yield ev("retry", reason="이번 사이클은 체결까지 가지 않았습니다 "
                                         "— 새 뉴스로 다시 시도합니다")

        # [5] 심사위원이 확인할 두 가지를 따로 뽑아 보여준다.
        #     ① 주식 토큰을 샀는가  ② API 를 호출 건당 결제했는가
        yield ev("summary-fill", filled=filled,
                 **_fill_summary(body.get("log", [])))

        async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                     headers=INTERNAL_HEADERS,
                                     timeout=30.0) as client:
            apis = await client.get(f"{BASE}/ui/bots/{bot_id}/apis")
        if apis.status_code == 200:
            a = apis.json()
            yield ev("summary-apis", summary=a.get("summary"),
                     connected=a.get("connected"))

        yield ev("done", balances=body.get("balances"), filled=filled)

    except Exception as e:                              # noqa: BLE001
        # 예외도 스트림으로 내보낸다. 연결만 끊기면 화면이 멈춘 것처럼
        # 보여서 무엇이 잘못됐는지 알 수 없다.
        yield ev("error", reason=f"{type(e).__name__}: {str(e)[:200]}")


@router.get("/buy")
async def buy(bot_id: str, draw: int = DEFAULT_DRAW) -> StreamingResponse:
    """매수 버튼. 심사위원의 서명은 필요 없다 — 그게 요점이다."""
    _ensure_devnet()
    if not await _restore(session):
        raise HTTPException(400, "지갑을 먼저 등록하세요")
    return StreamingResponse(
        _buy_events(bot_id, draw),
        media_type="text/event-stream",
        # 프록시가 버퍼링하면 실시간이 아니게 된다.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 매도·정산: 돈이 심사위원 지갑으로 돌아오는 것을 보여준다 ─────────
async def _sell_events(bot_id: str, payout: int | None, session: str):
    """전량 청산 → 정산 분배 → 심사위원 지갑으로 반환.

    [왜 이 화면이 필요한가]
    매수만 보여주면 "돈이 나가는 것"까지만 증명된다. 자동매매의 값어치는
    **돌아오는 쪽**에 있는데, 그동안 정산 결과는 봇 지갑 안에서만 움직여서
    심사위원 입장에서는 여전히 숫자놀음이었다. 본인 팬텀 지갑의 잔고가
    실제로 늘어나야 한 바퀴가 닫힌다.

    [무엇을 하지 않는가]
    손익을 만들어내지 않는다. 청산가는 매수 때와 같은 시세원(KIS 실시세)에서
    오고, 분배 비율(85/10/5)은 매수 시점 영수증에 박제된 값을 그대로 쓴다.
    여기서 하는 일은 '이미 정해진 정산을 집행하고 그 결과를 보여주는 것'뿐이다.
    """
    t0 = time.monotonic()
    wallet = judge_wallet(session)

    def ev(step: str, **kw) -> str:
        return _sse({"step": step, "t": round(time.monotonic() - t0, 2), **kw})

    try:
        from app.bots import BOTS
        from app.core.positions import BOOK
        from app.main import BASE, INTERNAL_HEADERS, app as fastapi_app

        bot = BOTS.get(bot_id)
        if bot is None:
            yield ev("blocked", reason=f"봇 없음: {bot_id}")
            return

        before = await LEDGER.delegate_status(wallet)
        yield ev("balance-before", judge_balance=before["balance"])

        # [1] 열린 포지션 전량 청산. 각 청산이 settle 을 타고, 이익이 나면
        #     85/10/5 로 분배된다 — 그 분배가 단일 트랜잭션인 것도 여기서 보인다.
        open_now = BOOK.of_bot(bot_id)
        yield ev("close-start", positions=len(open_now),
                 tickers=[p.ticker for p in open_now])

        if not open_now:
            yield ev("close-done", closed=0,
                     note="열린 포지션이 없습니다 — 정산할 것이 없어 반환만 진행합니다")
        else:
            transport = httpx.ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                         headers=INTERNAL_HEADERS,
                                         timeout=300.0) as client:
                r = await client.post(f"{BASE}/bots/{bot_id}/close-all")
            if r.status_code != 200:
                yield ev("blocked", reason=f"청산 실패 {r.status_code}: {r.text[:160]}")
                return

            body = r.json()
            realized = 0
            for res in body.get("results", []):
                if not isinstance(res, dict):
                    continue
                pnl = res.get("realized_pnl") or 0
                realized += pnl
                yield ev("settled",
                         ticker=res.get("ticker"), qty=res.get("qty"),
                         entry=res.get("entry_price"), exit=res.get("exit_price"),
                         realized_pnl=pnl,
                         distribution=res.get("distribution"),
                         tx=res.get("close_tx"),
                         explorer=EXPLORER.format(res["close_tx"])
                                  if res.get("close_tx") else None)
                await asyncio.sleep(0.15)
            yield ev("close-done", closed=body.get("closed"),
                     realized_pnl_total=realized)

        # [2] 심사위원 지갑으로 반환. 사용자 몫(85%)이 모여 있는
        #     user-treasury 에서 나간다.
        treasury = f"user-treasury@{bot_id}"
        available = (await LEDGER.snapshot()).get(treasury, 0)
        amount = available if payout is None else min(payout, available)
        yield ev("payout-start", frm=treasury, to=str(LEDGER.owner_pubkey(wallet)),
                 available=available, amount=amount)

        if amount <= 0:
            yield ev("blocked", reason="반환할 잔고가 없습니다")
            return

        proof = await LEDGER.transfer(treasury, wallet, amount,
                                      f"payout:{bot_id}")
        yield ev("payout-done", amount=amount, tx=proof.proof_id,
                 explorer=EXPLORER.format(proof.proof_id),
                 note="심사위원 지갑 잔고가 실제로 늘어납니다 — 팬텀에서 확인하세요")

        after = await LEDGER.delegate_status(wallet)
        yield ev("balance-after", judge_balance=after["balance"],
                 gained=after["balance"] - before["balance"])
        yield ev("done", judge_balance=after["balance"])

    except Exception as e:                                # noqa: BLE001
        yield ev("error", reason=f"{type(e).__name__}: {str(e)[:200]}")


@router.get("/sell")
async def sell(bot_id: str, payout: int | None = None,
               session: str = "") -> StreamingResponse:
    """매도·정산 버튼. 청산한 돈이 심사위원 지갑으로 돌아온다.

    payout 을 주지 않으면 user-treasury 잔고 전부를 반환한다.
    """
    _ensure_devnet()
    if not await _restore(session):
        raise HTTPException(400, "지갑을 먼저 등록하세요")
    return StreamingResponse(
        _sell_events(bot_id, payout),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
