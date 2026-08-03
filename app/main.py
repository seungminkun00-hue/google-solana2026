"""메인 앱 (Managed v2 — 멀티봇).

라우트 지도:
  GET  /bots                      봇 목록 + 요약 (요구사항 8)
  GET  /bots/{id}/state           봇 대시보드
  POST /bots/{id}/cycle           그 봇의 전체 사이클
  POST /bots/{id}/sell/signal/…   그 봇의 판매 창구 (x402)
  POST /bots/{id}/sell/thesis/…   〃
  POST /bots/{id}/replenish       인지비용 청구 (만다트 1)
  POST /admin/kill/{id}           봇 킬 스위치 (인증)
  POST /demo/seed                 전 봇 초기 자금 (인증)
  GET  /state                     전역 감사 (통화량·증빙)
"""
from __future__ import annotations

import os
import pathlib
import secrets
import time
import uuid

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from app.agents import pipeline
from app.bots import BOTS, DEMO_BOT_IDS, BotInstance, make_demo_bots
from app.core.journal import JOURNAL
from app.core.ledger import LEDGER, InsufficientFunds, RouteViolation
from app.core.mandate import issue_invoice
from app.core.proofs import PROOFS
from app.core.positions import BOOK, Position, should_close
from app.core.receipts import RECEIPTS
from app.core.scheduler import SCHEDULER
from app.core.session import session_id
from app.core import store as STORE
from app.core.x402_client import (BudgetExceeded, KillSwitchActive,
                                  PaymentFailed, SpendTracker, paid_fetch)
from app.core.x402_provider import paywall_dynamic
from app.external import router as mock_router, spot
from app.models import Rulebook, Signal, SpendPolicy, Thesis
from app.ui import router as ui_router
from pydantic import BaseModel, Field

app = FastAPI(title="Cognitive Economy v2 — self-funding trading bots")

# [프론트엔드 연동] 친구가 만든 UX가 다른 포트(예: 3000)에서 돌 때,
# 브라우저가 보안상 우리 API 호출을 차단한다. 이 설정이 그 빗장을 푼다.
# 해커톤 데모용이라 전체 허용. 실서비스에서는 allow_origins에
# 실제 도메인만 나열해야 한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # "*" 와 True 는 함께 쓸 수 없음
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(mock_router)
# 앱 화면 전용 조회 라우터. 자금 경로에는 관여하지 않는다 — app/ui.py 주석 참조.
app.include_router(ui_router)
# 심사위원 시연 창구(app/judge.py). 그쪽은 사이클을 돌리려고 app.main 을
# 되임포트하는데, 함수 안에서 지연 임포트하므로 순환이 생기지 않는다.
from app.judge import router as judge_router  # noqa: E402
app.include_router(judge_router)

# 소유자 전용 관제(app/owner.py). OWNER_TOKEN 을 준 배포에서만 라우트를
# 등록한다 — 안 준 배포에서는 경로 자체가 없어서 404 가 난다.
if os.environ.get("OWNER_TOKEN", "").strip():
    from app.owner import router as owner_router  # noqa: E402
    app.include_router(owner_router)

# [2026-08-02] 기동할 때 데모봇(bot1~3)을 자동으로 만들지 않는다.
#
# 심사위원이 앱을 열었을 때 남이 만든 봇 세 개가 이미 있으면, 직접 봇을
# 만들어 보는 흐름이 시작부터 사라진다. 빈 목록에서 "추가하기"로 시작해야
# 이 앱이 무엇을 하는 물건인지 손으로 알게 된다.
#
# 정의 자체는 남겨둔다. `POST /demo/seed` 가 여전히 make_demo_bots() 를
# 부르고, verify_scenario.py 와 audit.py 가 그 라우트로 bot1~3 을 만들어
# 검증한다 — 검증 경로는 그대로 살아 있다.
if os.environ.get("DEMO_BOTS", "0") == "1":
    make_demo_bots()

# 저장된 상태를 되살린다. make_demo_bots() 뒤여야 한다 —
# 데모봇 정의는 코드가, 사용자 봇은 파일이 진실원이다.
_restored = STORE.load()
if _restored["bots"]:
    print(f"  ↺ 상태 복원: 봇 {_restored['bots']}개"
          f"(신규 {len(_restored['restored_bots'])}) "
          f"영수증 {_restored['receipts']} 포지션 {_restored['positions']}")

# 관제 로그는 별도 파일이라 따로 되살린다. 봇이 하나도 복원되지 않아도
# (전부 지워졌어도) "누가 왔다 갔다" 는 남아 있어야 한다.
from app.core.ownerlog import OWNER_LOG  # noqa: E402
_log_rows = OWNER_LOG.restore()
if _log_rows:
    print(f"  ↺ 관제 기록 복원: {_log_rows}건")

SIGNALS: dict[str, Signal] = {}
THESES: dict[str, Thesis] = {}
BASE = "http://testserver"


# ── 자동매매 기동 ──────────────────────────────────────────────────
#
# [2026-08-03] 스케줄러를 서버가 뜰 때 함께 켠다.
#
# 예전에는 POST /scheduler/start 를 누군가 불러야만 돌았다. 그런데 이건
# **자동매매 봇**이다 — 사람이 매번 켜줘야 도는 자동화는 자동화가 아니고,
# 무엇보다 앱을 연 사람은 그런 라우트가 있는 줄도 모른다. 화면의 상태
# 램프가 늘 '꺼짐' 이면 표시등으로서 아무 뜻이 없다.
#
# 끄는 방법은 그대로 있다: 봇별 일시정지(앱 요약 탭)와 POST /scheduler/stop.
# 정지된 봇은 스케줄러가 건너뛰므로, 봇이 없거나 전부 정지면 아무 일도
# 일어나지 않는다 — 기동만 해두고 실제로 돌지 말지는 봇이 정한다.
AUTOSTART = os.environ.get("SCHEDULER_AUTOSTART", "1") != "0"
AUTOSTART_INTERVAL = int(os.environ.get("SCHEDULER_INTERVAL", "120"))


@app.on_event("startup")
async def _autostart_scheduler() -> None:
    if not AUTOSTART:
        print("  · 자동매매 자동 기동 꺼짐 (SCHEDULER_AUTOSTART=0)")
        return
    # start() 는 asyncio.create_task 를 쓰므로 이벤트 루프가 필요하다.
    # 모듈 최상단에서 부르면 루프가 아직 없어 죽는다 — 그래서 startup 이다.
    SCHEDULER.start(AUTOSTART_INTERVAL)
    print(f"  ▶ 자동매매 기동 (주기 {AUTOSTART_INTERVAL}초)")

# 외부 구매자 — 우리 시스템 밖의 에이전트를 대리한다.
#
# [2026-08] 예전에는 external-sale 단계가 LEDGER.transfer 직접 호출이었다.
# "외부가 우리 판단을 x402로 사간다"고 주장하면서 정작 402를 안 탔다.
# 이제 같은 페이월(/bots/{id}/sell/thesis/{id})을 통과한다 —
# 402 챌린지 → 정책 검사 → 결제 → 증빙 첨부 재요청 → 증빙 1회성 소비.
# 실제 서비스에서는 남의 서버가 이 자리에 오지만, 밟는 경로는 동일하다.
#
# 한도를 크게 잡는 이유: 이건 '외부 세계'의 지갑이라 우리 봇의 인지예산
# 정책을 적용받지 않는다. 봇의 한도는 research-agent 쪽에 걸려 있다.
EXTERNAL_BUYER = SpendTracker(
    wallet="external",
    policy=SpendPolicy(daily_cap=10**15, per_decision_cap=10**15,
                       session_expires_at=int(time.time()) + 86_400),
    auto_renew_seconds=86_400,
)


# ── 예외 → HTTP ─────────────────────────────────────────────────────
@app.exception_handler(RouteViolation)
async def _rv(request, exc):
    return JSONResponse(status_code=403,
                        content={"error": "지갑 격리 위반", "detail": str(exc)})


@app.exception_handler(InsufficientFunds)
async def _if(request, exc):
    return JSONResponse(status_code=409,
                        content={"error": "잔고 부족", "detail": str(exc)})


@app.exception_handler(PaymentFailed)
async def _pf(request, exc):
    return JSONResponse(status_code=502,
                        content={"error": "결제 후 요청 실패", "detail": str(exc)})


# ── 인증 ────────────────────────────────────────────────────────────
# [2026-08] 돈이 움직이는 라우트가 전부 무인증이었다. 특히 /settle 은
# receipt_id만 알면 누구나 남의 포지션을 임의 시점에 강제 청산할 수 있었고,
# CORS가 "*" 라 브라우저에서 바로 호출됐다.
#
# 문제는 이 라우트들을 우리 자신이 내부에서도 부른다는 것이다.
#   /cycle → /replenish,  /manage-positions → /settle,
#   /close-all → /settle,  스케줄러 → /manage-positions, /cycle
# 관리자 토큰을 내부 호출에 넣으면 "내부"와 "운영자"가 구분되지 않고,
# 토큰이 프로세스 안을 계속 돌아다니게 된다.
#
# 그래서 프로세스마다 새로 만드는 1회용 내부 토큰을 쓴다. 이 값은
# 메모리에만 있고 밖으로 나가지 않으므로 외부에서는 알 수 없다.
# 재시작하면 값이 바뀌어도 무해하다 — 내부 호출자도 같은 프로세스다.
INTERNAL_TOKEN = secrets.token_urlsafe(32)
INTERNAL_HEADERS = {"X-Internal-Call": INTERNAL_TOKEN}


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """운영자 전용. 내부 호출로도 우회할 수 없다."""
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(403, "관리자 인증 실패")


def require_operator(x_admin_token: str | None = Header(default=None),
                     x_internal_call: str | None = Header(default=None)) -> None:
    """운영자 토큰 또는 이 프로세스 내부 호출만 허용.

    자금이 움직이는 라우트에 붙인다. 내부 파이프라인(스케줄러·사이클·
    포지션 관리)은 X-Internal-Call 로 통과하고, 외부에서는
    X-Admin-Token 이 있어야 한다.
    """
    if x_internal_call is not None and secrets.compare_digest(
            x_internal_call, INTERNAL_TOKEN):
        return
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(403, {
            "error": "인증 필요",
            "hint": "자금이 움직이는 라우트입니다. X-Admin-Token 헤더가 필요합니다."})


def get_bot(bot_id: str) -> BotInstance:
    if bot_id not in BOTS:
        raise HTTPException(404, f"봇 없음: {bot_id}")
    return BOTS[bot_id]


async def _snapshot_equity(bot: BotInstance) -> None:
    """이 순간 이 봇의 총 평가액을 저널에 한 점 찍는다.

    수익률 곡선의 유일한 재료다. 자산이 실제로 움직인 직후 —
    체결·청산 — 에만 부른다. 주기적으로만 찍으면 매매 순간의 계단이
    사라져서 곡선이 실제와 다른 모양이 된다.
    """
    from app.external import spot
    snap = await LEDGER.snapshot()
    cash = sum(snap.get(bot.w(r), 0) for r in
               ("user-treasury", "invest-wallet", "research-agent",
                "revenue-wallet"))
    market = 0
    for p in BOOK.of_bot(bot.bot_id):
        market += p.qty * await spot(p.ticker) // 10**6
    JOURNAL.record_equity(bot.bot_id, cash + market, cash, market)


# ── 판매 창구 (봇별 x402 페이월) ────────────────────────────────────
def _revenue_of(request: Request) -> str:
    return f"revenue-wallet@{request.path_params['bot_id']}"


@app.post("/bots/{bot_id}/sell/signal/{signal_id}")
async def sell_signal(bot_id: str, signal_id: str,
                      _: str = Depends(paywall_dynamic(config.PRICE_SIGNAL, _revenue_of))):
    sig = SIGNALS.get(signal_id)
    if sig is None or sig.bot_id != bot_id:
        raise HTTPException(404, "signal not found")
    return sig.model_dump()


@app.post("/bots/{bot_id}/sell/thesis/{thesis_id}")
async def sell_thesis(bot_id: str, thesis_id: str,
                      _: str = Depends(paywall_dynamic(config.PRICE_THESIS, _revenue_of))):
    th = THESES.get(thesis_id)
    if th is None or th.bot_id != bot_id:
        raise HTTPException(404, "thesis not found")
    receipt = RECEIPTS.receipts.get(th.receipt_id)
    if receipt and not receipt.receipt_complete:
        # 영수증이 뒷받침하지 못하는 판단은 팔지 않는다.
        # degraded = managed로 선언했는데 실제로는 모의 판단으로 폴백한 것.
        raise HTTPException(409, {
            "error": "영수증 불완전 — 판매 불가",
            "inference_mode": receipt.inference_mode,
            "sources": receipt.inference_sources,
            "hint": "선언한 추론과 실제 추론이 다릅니다 — 모의 판단으로 "
                    "폴백한 결정은 팔지 않습니다."})

    # [출처 공개] 무엇이 이 판단을 만들었는지 사는 쪽이 볼 수 있어야 한다.
    # Gemini가 실패해 모의 판단으로 폴백했다면 degraded=true가 나간다.
    # 파는 물건이 '판단'인 이상, 그 판단의 출처를 숨기면 사기다.
    payload = th.for_sale().model_dump()
    if receipt:
        payload["provenance"] = {
            "inference_mode": receipt.inference_mode,
            "sources": receipt.inference_sources,
            "degraded": receipt.degraded,
        }
    return payload


# ── 인지비용 청구 (만다트 1) ────────────────────────────────────────
@app.post("/bots/{bot_id}/replenish")
async def replenish(bot_id: str, needed: int = config.RESEARCH_DAILY_CAP,
                    _: None = Depends(require_operator)):
    """리서치 예산을 '하루치'까지 채운다.

    이전에는 결정당 상한의 2배(=$0.20)만 목표로 잡아서,
    정산 배분이 조금만 쌓여도 "부족액 없음"이 되어 청구가 발동하지 않았다.
    에이전트가 채워야 할 것은 결정 한두 건 분이 아니라 하루 운영 예산이다.
    """
    bot = get_bot(bot_id)
    balance = (await LEDGER.snapshot()).get(bot.w("research-agent"), 0)
    inv = issue_invoice(
        kind="cognitive", issuer=bot.w("research-agent"),
        payer=bot.w("revenue-wallet"), balance=balance, needed=needed,
        expected_value=needed * 8,
        reason=f"일일 리서치 예산 (잔고 {balance})")
    result = await bot.cog_mandate.process(inv)
    return {"invoice": result.model_dump(), "balances": await bot.balances()}


# ── 봇 사이클 ───────────────────────────────────────────────────────
@app.post("/bots/{bot_id}/cycle")
async def bot_cycle(bot_id: str, _: None = Depends(require_operator)):
    """진행 상황 중계를 켜고 본체를 돌린다.

    본체를 따로 뺀 이유는 return 지점이 여러 개라서다. try/finally 로
    감싸야 어디로 빠져나가든 '진행 중' 표시가 반드시 꺼진다 — 안 그러면
    중간에 막힌 사이클이 화면에서 영원히 도는 것처럼 보인다.
    """
    from app.core.progress import PROGRESS
    get_bot(bot_id)                    # 없는 봇이면 여기서 404
    PROGRESS.start(bot_id, "사이클")
    try:
        return await _bot_cycle_body(bot_id)
    finally:
        PROGRESS.done(bot_id)


async def _bot_cycle_body(bot_id: str):
    bot = get_bot(bot_id)
    # 평범한 list 대신 ProgressLog 를 쓴다. append 될 때마다 진행 상황
    # 레지스트리로 흘러가서, 화면이 사이클이 **끝나기 전에도** 단계를
    # 볼 수 있다. devnet 사이클은 30~120초라 그 전까지 화면이 완전히
    # 비어 있었다 — "눌렀는데 아무것도 안 뜬다" 의 정체였다.
    # 아래 log.append 들은 하나도 손대지 않는다.
    from app.core.progress import ProgressLog
    log = ProgressLog(bot_id)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 headers=INTERNAL_HEADERS) as client:

        # [0] 예산 점검 — 생각하기 전에 지갑부터 본다.
        # 이전에는 지출부터 하고 나중에 보충했는데, 리서치 지갑이 0이면
        # 첫 뉴스조차 못 산다. 사람도 지갑을 먼저 확인하고 나간다.
        pre = await client.post(f"{BASE}/bots/{bot_id}/replenish")
        pre_inv = pre.json()["invoice"]
        log.append({"step": "budget-check",
                    **{k: pre_inv[k] for k in ("status", "amount", "decided_reason")}})

        # [1] Scout — MVoT 통과까지 최대 5회
        signal, meta = None, {}
        for _ in range(5):
            try:
                signal, meta = await pipeline.scout_run(client, BASE, bot)
            except (BudgetExceeded, KillSwitchActive) as e:
                log.append({"step": "scout", "blocked": str(e)})
                return {"log": log, "balances": await bot.balances()}
            log.append({"step": "scout",
                        **{k: v for k, v in meta.items() if k != "_proofs"}})
            if signal:
                break
        if signal is None:
            log.append({"step": "scout", "result": "가치있는 시그널 없음 — 지출 중단이 알파"})
            return {"log": log, "balances": await bot.balances()}
        SIGNALS[signal.signal_id] = signal

        # [2] Analyst — 시그널 실구매 + Deep
        try:
            thesis = await pipeline.analyst_run(
                client, BASE, bot, signal.signal_id, seller_bot_id=bot_id,
                scout_proofs=meta.get("_proofs"),
                scout_sources=meta.get("_sources"))
        except (BudgetExceeded, KillSwitchActive) as e:
            log.append({"step": "analyst", "blocked": str(e)})
            return {"log": log, "balances": await bot.balances()}
        THESES[thesis.thesis_id] = thesis
        log.append({"step": "analyst", "ticker": thesis.ticker,
                    "side": thesis.side, "confidence": thesis.confidence})

        # 외부 에이전트가 우리 테제를 x402로 구매한다.
        # 직접 이체가 아니라 판매 창구를 그대로 통과한다 —
        # 402 → 정책검사 → 결제 → 증빙 첨부 재요청 → 증빙 1회성 소비.
        #
        # 판매가 실패해도 사이클을 멈추지 않는다. 안 팔린 것이지 판단이
        # 틀린 게 아니다. 영수증이 불완전하면(degraded) 창구가 409로
        # 거절하는데, 그건 우리가 의도한 동작이다.
        EXTERNAL_BUYER.begin_decision()
        try:
            _, ext_proof = await paid_fetch(
                client, f"{BASE}/bots/{bot_id}/sell/thesis/{thesis.thesis_id}",
                EXTERNAL_BUYER)
            log.append({"step": "external-sale", "via": "x402",
                        "amount": ext_proof.amount if ext_proof else 0})
        except (PaymentFailed, BudgetExceeded, KillSwitchActive) as e:
            log.append({"step": "external-sale", "blocked": str(e)[:140]})

        # [3] 만다트 1: 인지비용 보충
        rep = await client.post(f"{BASE}/bots/{bot_id}/replenish")
        log.append({"step": "replenish",
                    **{k: rep.json()["invoice"][k] for k in ("status", "amount", "decided_reason")}})

        # [4] 룰북 게이트
        try:
            pipeline.rulebook_gate(bot, thesis)
        except pipeline.PolicyRejected as e:
            log.append({"step": "rulebook", "blocked": str(e)})
            return {"log": log, "balances": await bot.balances()}
        log.append({"step": "rulebook", "passed": True})

        # [5] 만다트 2: 매매자금 청구 (부족 시)
        cap = await pipeline.request_capital(bot, thesis)
        if cap:
            log.append({"step": "capital-invoice",
                        **{k: cap[k] for k in ("status", "amount", "decided_reason")}})
            if cap["status"] != "approved":
                return {"log": log, "balances": await bot.balances()}

        # [6] 체결 — USDC를 내고 미러 주식 토큰을 실제로 받는다.
        # 수량 = 투입금 ÷ 시세. 소수점 6자리까지 쪼갤 수 있다.
        qty = thesis.size_usdc * 10**6 // thesis.price_micro
        if qty <= 0:
            log.append({"step": "executor", "blocked": "체결 수량 0 — 투입금이 1주 미만"})
            return {"log": log, "balances": await bot.balances()}
        proof = await LEDGER.swap_in(bot.w("invest-wallet"), thesis.ticker,
                                     thesis.size_usdc, qty)
        receipt = RECEIPTS.receipts[thesis.receipt_id]
        receipt.execution_tx = proof.proof_id
        receipt.position_size = thesis.size_usdc
        root = RECEIPTS.anchor(thesis.receipt_id)
        BOOK.open(Position(
            receipt_id=thesis.receipt_id, bot_id=bot_id, ticker=thesis.ticker,
            qty=qty, basis=thesis.size_usdc, entry_price=thesis.price_micro,
            tx=proof.proof_id))
        bot.trades_today += 1

        # [화면용 기록] 체결은 여기서만 일어난다. 포지션은 청산되면
        # 장부에서 사라지므로, 남기지 않으면 '거래 내역'을 복원할 방법이
        # 없다. 기록 실패가 매매를 되돌리게 하지는 않는다.
        try:
            JOURNAL.record_fill(
                bot_id=bot_id, ticker=thesis.ticker, side="buy", qty=qty,
                price_micro=thesis.price_micro, gross_micro=thesis.size_usdc,
                receipt_id=thesis.receipt_id, tx=proof.proof_id)
            await _snapshot_equity(bot)
        except Exception as e:                        # noqa: BLE001
            print(f"  ⚠️ 체결 기록 실패: {str(e)[:80]}")
        # 포지션이 열린 직후에 저장한다. 여기서 프로세스가 죽으면
        # 온체인에는 토큰이 있는데 장부에는 없는 상태가 된다.
        STORE.save()
        log.append({"step": "executor", "ticker": thesis.ticker,
                    "qty": qty / 10**6, "entry_price": thesis.price_micro / 10**6,
                    "anchor_root": root})

        # [7] 포지션 유지 — 사자마자 팔지 않는다.
        # 청산은 /manage-positions 가 룰북 조건(익절·손절·보유시간)에
        # 따라 판단한다. 그게 자동 투자와 1회성 테스트의 차이다.
        log.append({"step": "position-open", "ticker": thesis.ticker,
                    "qty": qty / 10**6,
                    "entry_price": thesis.price_micro / 10**6,
                    "note": "룰북 청산조건 충족 시 자동 매도"})

    return {"log": log, "balances": await bot.balances()}


# ── 수동 주문 (사용자가 대화로 직접 시킨 것) ────────────────────────
async def manual_buy(bot: BotInstance, ticker: str, size_usdc: int,
                     reason: str) -> dict:
    """룰북 게이트를 거치지 않고 바로 산다.

    [왜 룰북을 안 보는가]
    룰북은 **에이전트의 자율 판단**에 걸리는 규칙이다. "확신도 0.8 미만이면
    사지 마라" 는 봇이 스스로 판단할 때의 기준이지, 소유자가 직접 내리는
    주문까지 막으라는 뜻이 아니다. 증권사 앱에서 자동매매 조건을 걸어둬도
    본인이 시장가 주문을 내는 것은 언제나 되는 것과 같다.

    그래서 이 경로는 에이전트의 주장을 훼손하지 않는다 — 다만 **반드시
    구분해서 기록한다**(receipt.manual). 구분이 없으면 나중에 이 체결을
    보고 "봇이 룰북을 어겼다"고 읽히고, 그건 사실이 아니다.

    [그래도 지키는 것]
      · 돈은 있어야 산다 (없는 돈으로는 체결되지 않는다)
      · 미러 토큰이 있는 종목만 (없는 것은 애초에 살 수 없다)
      · 자금 경로 규칙은 그대로 — user-treasury → invest-wallet 위임을 거친다
    """
    from app.core.markets import quote_spec
    if quote_spec(ticker) is None:
        raise HTTPException(400, {
            "error": "거래할 수 없는 종목",
            "ticker": ticker,
            "hint": "devnet 에 미러 토큰이 있는 종목만 살 수 있습니다."})

    price = await spot(ticker)
    qty = size_usdc * 10**6 // price
    if qty <= 0:
        raise HTTPException(400, {
            "error": "주문 금액이 1주 값보다 작습니다",
            "price_usd": price / 10**6})

    # 투자지갑에 돈이 모자라면 트레저리에서 위임한다. 만다트 심사를
    # 거치지 않는다 — 심사는 에이전트가 청구할 때 하는 것이고,
    # 이건 소유자가 자기 돈을 자기 계좌 안에서 옮기는 것이다.
    snap = await LEDGER.snapshot()
    have = snap.get(bot.w("invest-wallet"), 0)
    if have < size_usdc:
        need = size_usdc - have
        treasury = snap.get(bot.w("user-treasury"), 0)
        if treasury < need:
            raise HTTPException(409, {
                "error": "잔고 부족",
                "need_usdc": round(size_usdc / 10**6, 2),
                "have_usdc": round((have + treasury) / 10**6, 2)})
        await LEDGER.transfer(bot.w("user-treasury"), bot.w("invest-wallet"),
                              need, f"manual-order:{bot.bot_id}")

    proof = await LEDGER.swap_in(bot.w("invest-wallet"), ticker, size_usdc, qty)

    # 영수증은 만든다 — 정산할 때 분배표가 여기 있어야 하기 때문이다.
    # 다만 manual 로 찍어 에이전트의 성적표에서는 빠진다.
    receipt = RECEIPTS.create(
        bot_id=bot.bot_id, source_urls=[], prompt=f"manual:{reason}",
        output=f"manual buy {ticker} {size_usdc}", proofs=[],
        policy_snapshot={"rulebook": bot.rulebook.model_dump(mode="json"),
                         "manual": True},
        splits_bps=bot.splits, declared_mode=config.INFERENCE_MODE,
        inference_sources={})
    receipt.manual = True
    receipt.manual_reason = reason[:200]
    receipt.execution_tx = proof.proof_id
    receipt.position_size = size_usdc

    BOOK.open(Position(receipt_id=receipt.receipt_id, bot_id=bot.bot_id,
                       ticker=ticker, qty=qty, basis=size_usdc,
                       entry_price=price, tx=proof.proof_id))
    bot.trades_today += 1
    try:
        JOURNAL.record_fill(bot_id=bot.bot_id, ticker=ticker, side="buy",
                            qty=qty, price_micro=price, gross_micro=size_usdc,
                            receipt_id=receipt.receipt_id, tx=proof.proof_id,
                            reason="manual")
        await _snapshot_equity(bot)
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 수동 매수 기록 실패: {str(e)[:80]}")
    STORE.save()

    return {"side": "buy", "ticker": ticker, "qty": qty / 10**6,
            "price_usd": price / 10**6, "size_usd": size_usdc / 10**6,
            "tx": proof.proof_id, "receipt_id": receipt.receipt_id,
            "manual": True}


async def manual_sell(bot: BotInstance, ticker: str, reason: str) -> dict:
    """그 종목의 열린 포지션을 전부 청산한다. 룰북 조건은 보지 않는다."""
    targets = [p for p in BOOK.of_bot(bot.bot_id)
               if p.ticker == ticker or p.ticker == f"{ticker}x"]
    if not targets:
        raise HTTPException(409, {
            "error": "그 종목의 보유 포지션이 없습니다", "ticker": ticker})

    results = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 timeout=300, headers=INTERNAL_HEADERS) as c:
        for pos in targets:
            r = await c.post(f"{BASE}/settle/{pos.receipt_id}",
                             params={"reason": f"manual:{reason[:60]}"})
            results.append(r.json() if r.status_code == 200 else r.text[:120])
    return {"side": "sell", "ticker": targets[0].ticker,
            "closed": len(results), "results": results, "manual": True}


# ── 포지션 관리 (자동 청산) ─────────────────────────────────────────
@app.post("/bots/{bot_id}/manage-positions")
async def manage_positions(bot_id: str, _: None = Depends(require_operator)):
    """열린 포지션을 점검하고 룰북 조건에 맞으면 청산한다.

    사람이 안 보고 있을 때 손실을 끊고 이익을 확정하는 장치.
    스케줄러가 매 주기 이걸 먼저 호출한다.
    """
    bot = get_bot(bot_id)
    actions = []
    for pos in BOOK.of_bot(bot_id):
        price = await spot(pos.ticker)
        close, reason = should_close(pos, price, bot.rulebook)
        entry = {"receipt_id": pos.receipt_id, "ticker": pos.ticker,
                 "pnl_pct": round(pos.pnl_pct(price), 2),
                 "held_hours": round(pos.held_hours(), 2), "reason": reason}
        if close:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                         timeout=120,
                                         headers=INTERNAL_HEADERS) as c:
                # 청산 사유를 함께 넘긴다. 이 문자열은 여기서만 만들어지고
                # settle 쪽에서는 다시 계산할 수 없다(포지션이 곧 닫힌다).
                r = await c.post(f"{BASE}/settle/{pos.receipt_id}",
                                 params={"reason": reason})
            entry["closed"] = True
            entry["settle"] = r.json() if r.status_code == 200 else r.text[:150]
        actions.append(entry)
    return {"bot_id": bot_id, "open_positions": len(BOOK.of_bot(bot_id)),
            "actions": actions}


# ── 정산 ────────────────────────────────────────────────────────────
@app.post("/settle/{receipt_id}")
async def settle(receipt_id: str, reason: str = "",
                 _: None = Depends(require_operator)):
    pos = BOOK.get(receipt_id)
    if pos is None:
        raise HTTPException(404, "position not found")
    bot = get_bot(pos.bot_id)

    # 청산 시점 시세. 손익이 난수가 아니라
    # '진입가 대비 실제 가격 변동'에서 나온다.
    exit_price = await spot(pos.ticker)

    proceeds = pos.qty * exit_price // 10**6
    pnl = proceeds - pos.basis
    proof = await LEDGER.swap_out(bot.w("invest-wallet"), pos.ticker,
                                  pos.qty, proceeds)
    result = await RECEIPTS.settle(receipt_id, pnl, bot.w("invest-wallet"))
    held = pos.held_hours()
    result.update({"close_tx": proof.proof_id,
                   # 종목명이 없으면 청산 결과만 보고는 무엇을 팔았는지 알 수
                   # 없다. 정산 화면이 그대로 쓰는 값이라 여기서 실어 보낸다.
                   "ticker": pos.ticker,
                   "entry_price": pos.entry_price / 10**6,
                   "exit_price": exit_price / 10**6,
                   "qty": pos.qty / 10**6,
                   "held_hours": round(held, 2)})

    # [화면용 기록] 청산가·실현손익·보유시간은 이 순간에만 존재한다.
    # BOOK.close 이후에는 어디에도 남지 않으므로 먼저 적는다.
    try:
        JOURNAL.record_fill(
            bot_id=pos.bot_id, ticker=pos.ticker, side="sell", qty=pos.qty,
            price_micro=exit_price, gross_micro=proceeds,
            receipt_id=receipt_id, tx=proof.proof_id, pnl_micro=pnl,
            reason=reason or "manual", hold_hours=round(held, 3))
    except Exception as e:                            # noqa: BLE001
        print(f"  ⚠️ 청산 기록 실패: {str(e)[:80]}")

    BOOK.close(receipt_id)
    STORE.save()
    try:
        await _snapshot_equity(bot)
    except Exception as e:                            # noqa: BLE001
        print(f"  ⚠️ 자산 스냅샷 실패: {str(e)[:80]}")
    return result


# ── 대시보드 (요구사항 8) ───────────────────────────────────────────
@app.get("/bots")
async def list_bots():
    out = []
    for b in BOTS.values():
        stats = RECEIPTS.stats(b.bot_id)
        bal = await b.balances()
        out.append({
            "bot_id": b.bot_id, "owner": b.owner, "label": b.rulebook.label,
            "hit_rate": round(stats["hit_rate"], 2), "samples": stats["samples"],
            "cold_start": stats["cold_start"],
            "balances_usdc": bal,
            "self_funding": bal["revenue-wallet"] > 0,
        })
    return {"bots": out, "inference_mode": config.INFERENCE_MODE}


@app.get("/bots/{bot_id}/state")
async def bot_state(bot_id: str):
    bot = get_bot(bot_id)
    stats = RECEIPTS.stats(bot_id)
    receipts = [r for r in RECEIPTS.receipts.values() if r.bot_id == bot_id]
    return {
        "bot_id": bot_id, "owner": bot.owner,
        "rulebook": bot.rulebook.model_dump(mode="json"),
        "balances_usdc": await bot.balances(),
        "spent_today": bot.tracker.spent_today,
        "trades_today": bot.trades_today,
        "performance": stats,
        "open_positions": len(BOOK.of_bot(bot_id)),
        "decisions": len(receipts),
        "settled": sum(1 for r in receipts if r.settled_at),
        # 선언은 managed였는데 실제로는 폴백한 결정의 수.
        # 0이 아니면 "진짜 추론을 샀다"는 주장이 그만큼 약해진다.
        "degraded_decisions": sum(1 for r in receipts if r.degraded),
    }


@app.get("/state")
async def global_state():
    return {"supply_audit": await LEDGER.audit_supply(),
            "proof_registry": PROOFS.stats(),
            "anchors": len(RECEIPTS.anchors),
            "inference_mode": config.INFERENCE_MODE}


# ══════ 사용자 봇 생성 (앱에서 호출하는 API) ═══════════════════════
class CreateBotRequest(BaseModel):
    """사용자가 앱에서 봇을 만들 때 보내는 것.

    금액 한도와 룰북(매수·매도 지침)만 정하면 봇이 생성된다.
    """
    owner: str = Field(..., description="소유자 이름")
    label: str = Field("", description="봇 별명 (예: 반도체 집중형)")

    # 자금 한도
    deposit_usdc: float = Field(..., gt=0, description="예치 금액 (USD)")

    # 매수 규칙
    tickers: list[str] = Field(..., min_length=1, description="투자 종목")
    min_confidence: float = Field(0.8, ge=0.0, le=1.0)
    max_position_usd: float = Field(50.0, gt=0, description="1회 최대 투입")
    max_trades_per_day: int = Field(5, ge=1, le=100)

    # 매도 규칙 (자동 청산)
    take_profit_pct: float = Field(5.0, gt=0, description="+N%면 익절")
    stop_loss_pct: float = Field(3.0, gt=0, description="-N%면 손절")
    max_hold_hours: float = Field(24.0, gt=0, description="N시간 넘으면 청산")


@app.post("/bots")
async def create_bot(req: CreateBotRequest, _: None = Depends(require_admin),
                     session: str = Depends(session_id)):
    """앱에서 봇을 생성한다.

    session 은 만든 브라우저를 가리킨다. 헤더가 없으면 빈 값이 되고,
    그 봇은 모두에게 보이는 공용 봇이 된다 — 검증 스크립트가 그 경로다.
    """
    from app.adapters import universe

    # ① 종목 검증
    tickers = {t if t.endswith("x") else f"{t}x" for t in req.tickers}
    unknown = [t for t in tickers if not universe.is_tracked(t)]
    if unknown:
        raise HTTPException(400, {
            "error": "추적 대상이 아닌 종목",
            "unknown": unknown,
            "hint": "smartmoney.market SEC 추적 목록(1,045종목)에 없습니다."})

    # ② 룰북 생성
    rb = Rulebook(
        label=req.label or f"{req.owner}의 봇",
        allowed_tickers=tickers,
        min_confidence=req.min_confidence,
        max_position_usdc=int(req.max_position_usd * config.USDC),
        max_trades_per_day=req.max_trades_per_day,
        take_profit_pct=req.take_profit_pct,
        stop_loss_pct=req.stop_loss_pct,
        max_hold_hours=req.max_hold_hours,
    )

    # ③ 봇 생성 — 준비가 끝나기 전에는 BOTS에 넣지 않는다.
    #
    # [수정 2026-08] 예전에는 여기서 바로 BOTS[bot_id]=bot 을 했다.
    # 뒤의 지갑 생성이나 예치가 실패하면 사용자에게는 500이 가는데
    # 서버에는 지갑 없는 반쪽 봇이 남았다. 스케줄러가 그 봇을 계속
    # 돌리다 5회 실패 후 auto_killed 로 정지시킨다 — 사용자는 "만들다
    # 실패했다"고 아는데 서버는 좀비를 붙잡고 있는 상태.
    # 실제로 이 경로로 만들어진 고아 봇이 devnet에 여럿 남아 있었다.
    bot_id = f"bot_{uuid.uuid4().hex[:8]}"
    bot = BotInstance(bot_id=bot_id, owner=req.owner, rulebook=rb,
                      session=session)

    # ④ devnet이면 지갑과 토큰 계정을 만든다
    if hasattr(LEDGER, "ensure_bot_wallets"):
        await LEDGER.ensure_bot_wallets(bot_id)

    # ⑤ 예치
    deposit = int(req.deposit_usdc * config.USDC)
    await LEDGER.transfer("external", bot.w("user-treasury"),
                          deposit, f"deposit:{bot_id}")
    await LEDGER.transfer("external", bot.w("revenue-wallet"),
                          300_000, f"founding-capital:{bot_id}")

    # ⑥ 여기까지 왔으면 봇은 실제로 쓸 수 있는 상태다. 이제 등록한다.
    BOTS[bot_id] = bot
    STORE.save()
    return {"bot_id": bot_id, "owner": bot.owner,
            "rulebook": rb.model_dump(mode="json"),
            "balances_usdc": await bot.balances(),
            "note": "research-agent는 0에서 시작합니다 — 인보이스로만 조달"}


@app.delete("/bots/{bot_id}")
async def delete_bot(bot_id: str, _: None = Depends(require_admin)):
    """봇 정지 및 제거. 열린 포지션은 먼저 청산해야 한다."""
    bot = get_bot(bot_id)
    open_pos = BOOK.of_bot(bot_id)
    if open_pos:
        raise HTTPException(409, {
            "error": "열린 포지션이 있습니다",
            "count": len(open_pos),
            "hint": "POST /bots/{bot_id}/close-all 로 먼저 청산하세요."})
    bot.killed = True
    del BOTS[bot_id]
    # 프로필도 함께 지운다. 남겨두면 같은 bot_id 가 재사용될 때 남의
    # 이름·프롬프트가 붙은 봇이 생긴다.
    from app.core.profiles import PROFILES
    PROFILES.drop(bot_id)
    STORE.save()
    return {"deleted": bot_id}


@app.post("/bots/{bot_id}/close-all")
async def close_all(bot_id: str, _: None = Depends(require_operator)):
    """이 봇의 모든 포지션을 즉시 청산한다 (룰북 조건 무시).

    사용자가 "지금 다 팔아"라고 할 때 쓰는 비상 탈출구.
    """
    get_bot(bot_id)
    results = []
    # raise_app_exceptions=False 가 중요하다.
    #
    # [버그 2026-08-03] 기본값은 True 라, /settle 안에서 처리되지 않은
    # 예외가 나면 **여기까지 그대로 올라온다**. devnet 청산은 실패할 수
    # 있는 일인데(Blockhash not found·토큰계정 없음 등) 그때마다 이 라우트
    # 전체가 plain text "Internal Server Error" 500 이 됐다.
    # 그 응답은 JSON 이 아니라서 화면은 `Unexpected token 'I'` 라는,
    # 원인과 아무 상관 없는 메시지만 보여줬다 — 포지션 하나가 안 팔린다는
    # 사실이 화면에 도달할 방법이 없었다.
    #
    # False 로 두면 하위 요청의 예외가 500 '응답' 이 되어 여기서 읽을 수
    # 있다. 한 건이 실패해도 나머지는 계속 청산한다.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 timeout=120, headers=INTERNAL_HEADERS) as c:
        for pos in BOOK.of_bot(bot_id):
            row = {"receipt_id": pos.receipt_id, "ticker": pos.ticker}
            try:
                r = await c.post(f"{BASE}/settle/{pos.receipt_id}")
                if r.status_code == 200:
                    row.update(ok=True, **r.json())
                else:
                    row.update(ok=False, error=r.text[:300],
                               status=r.status_code)
            except Exception as e:                    # noqa: BLE001
                row.update(ok=False,
                           error=f"{type(e).__name__}: {str(e)[:300]}")
            results.append(row)
    ok = [x for x in results if x.get("ok")]
    bad = [x for x in results if not x.get("ok")]
    # closed 는 '실제로 청산된 수' 다. 예전에는 시도한 수를 세서, 전부
    # 실패해도 "3건 청산" 이라고 보고했다.
    return {"closed": len(ok), "failed": len(bad), "results": results}


# ══════ 자동 실행 스케줄러 ═════════════════════════════════════════
@app.post("/scheduler/start")
async def scheduler_start(interval_seconds: int = 300,
                          _: None = Depends(require_admin)):
    """자동 실행을 켠다. 이후 사람 개입 없이 계속 돈다."""
    return SCHEDULER.start(interval_seconds)


@app.post("/scheduler/stop")
async def scheduler_stop(_: None = Depends(require_admin)):
    return SCHEDULER.stop()


@app.get("/scheduler/status")
async def scheduler_status():
    return {**SCHEDULER.status(), "recent": SCHEDULER.log[-20:]}


# ── 관리자 ──────────────────────────────────────────────────────────
@app.post("/admin/kill/{bot_id}")
async def kill_bot(bot_id: str, on: bool = True, _: None = Depends(require_admin)):
    bot = get_bot(bot_id)
    bot.killed = on
    bot.tracker.policy.killed = on
    STORE.save()
    return {"bot_id": bot_id, "killed": on}


@app.get("/health")
async def health():
    """살아 있는가. 호스팅 업체의 헬스체크가 이걸 본다.

    원장·추론·시세가 실제로 붙었는지까지 알려준다 — 200 만 돌려주면
    '떠 있지만 아무것도 못 하는' 상태를 못 걸러낸다.
    """
    from app.adapters import gemini_byok, kis_quotes
    return {
        "ok": True,
        "ledger": os.environ.get("LEDGER_MODE", "mock").lower(),
        "bots": len(BOTS),
        "inference_live": gemini_byok.enabled(),
        "quotes_live": kis_quotes.enabled(),
    }


@app.post("/demo/seed")
async def seed(_: None = Depends(require_admin)):
    """데모봇 3개(bot1~3)를 시작 상태로 되돌린다.

    ⚠️ 사용자가 만든 봇은 건드리지 않는다. 예전에는 make_demo_bots()가
       BOTS.clear()로 시작하고 이 함수가 전 봇을 순회해서, 시연 중
       seed를 한 번 더 누르면 사용자 봇이 사라지고 온체인 자금이
       좌초됐다. 이제 대상은 DEMO_BOT_IDS 로 한정된다.


    ⚠️ mock과 devnet의 결정적 차이:
       mock은 프로세스를 껐다 켜면 잔고가 초기화되지만,
       온체인 상태는 실행할 때마다 누적된다.
       그래서 단순히 '더하기'를 하면 회차마다 잔고가 불어나
       인보이스가 영원히 발동하지 않는다.

    해법: 목표 잔고를 정하고 부족분만 채운다. 초과분은 되돌린다.
          덕분에 몇 번을 돌려도 항상 같은 지점에서 시작한다.

    설계 의도:
      user-treasury  $500  사용자 원금
      invest-wallet  $5    일부러 적게 — 매매 때마다 자본 청구가 발생하도록
      research-agent 0     반드시 인보이스로만 조달 (자급자족 증명)
      revenue-wallet 그대로 벌어들인 돈은 건드리지 않는다 (생존 지표)
    """
    TARGET_TREASURY = 500 * config.USDC
    TARGET_INVEST = 5 * config.USDC
    # 판매수익 지갑의 창업 자본 하한. 회사도 첫 매출 전까지 버틸
    # 종잣돈이 필요하다. 이미 벌어둔 게 더 많으면 건드리지 않는다.
    MIN_REVENUE_FLOAT = 300_000

    make_demo_bots()
    # 데모봇의 시그널·테제만 지운다. 전부 지우면 사용자 봇의 판매 창구가
    # 조용히 404가 되고, 그 봇이 이미 판 물건의 근거도 사라진다.
    for store in (SIGNALS, THESES):
        for k in [k for k, v in store.items() if v.bot_id in DEMO_BOT_IDS]:
            del store[k]

    report = {}
    for bot_id in DEMO_BOT_IDS:
        bot = BOTS[bot_id]
        snap = await LEDGER.snapshot()
        tre = snap.get(bot.w("user-treasury"), 0)
        inv_bal = snap.get(bot.w("invest-wallet"), 0)

        # 투자지갑 초과분은 트레저리로 되돌린다 (허용된 경로)
        if inv_bal > TARGET_INVEST:
            await LEDGER.transfer(bot.w("invest-wallet"), bot.w("user-treasury"),
                                  inv_bal - TARGET_INVEST, "reset:invest-excess")
            tre += inv_bal - TARGET_INVEST
            inv_bal = TARGET_INVEST

        # 트레저리를 목표치까지 채운다 (부족할 때만)
        if tre < TARGET_TREASURY:
            await LEDGER.transfer("external", bot.w("user-treasury"),
                                  TARGET_TREASURY - tre, "onramp:deposit")
            tre = TARGET_TREASURY

        # 투자지갑을 목표치까지 위임
        if inv_bal < TARGET_INVEST:
            await LEDGER.transfer(bot.w("user-treasury"), bot.w("invest-wallet"),
                                  TARGET_INVEST - inv_bal, "delegate:initial")

        # 판매수익 지갑: 벌어둔 게 하한 미만일 때만 창업 자본을 넣는다.
        rev = (await LEDGER.snapshot()).get(bot.w("revenue-wallet"), 0)
        if rev < MIN_REVENUE_FLOAT:
            await LEDGER.transfer("external", bot.w("revenue-wallet"),
                                  MIN_REVENUE_FLOAT - rev, "seed:founding-capital")

        report[bot.bot_id] = await bot.balances()

    STORE.save()
    return {"seeded": list(DEMO_BOT_IDS),
            "preserved": [b for b in BOTS if b not in DEMO_BOT_IDS],
            "balances": report,
            "note": "research-agent는 시드하지 않습니다 — 인보이스로만 조달. "
                    "사용자 생성 봇은 초기화 대상이 아닙니다."}


# ══════ 프론트 서빙 (배포용) ═══════════════════════════════════════
#
# [왜 같은 서버가 화면까지 주나]
# 정적 호스팅과 API 서버를 따로 띄우면 배포가 둘, 주소가 둘, CORS 설정이
# 하나 더 늘고, 심사위원에게 줄 링크도 어느 쪽인지 헷갈린다.
# 화면은 빌드된 정적 파일 몇 개뿐이라 이 프로세스가 같이 주면 그만이다.
# 같은 출처가 되므로 CORS 도, VITE_API_BASE 도 신경 쓸 것이 없어진다.
#
# web/dist 가 없으면(개발 중) 아무것도 하지 않는다 — 개발은 vite dev 가
# 5173 에서 화면을, 이 서버가 8100 에서 API 를 맡는 구성 그대로다.
_DIST = pathlib.Path("web") / "dist"

if _DIST.is_dir():
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        """SPA 폴백.

        /bot/xxx 같은 주소는 서버에 없는 경로다. 브라우저가 새로고침하면
        404 가 나므로 index.html 을 돌려주고 라우팅은 브라우저가 한다.

        ⚠️ 이 라우트는 **맨 마지막**에 등록돼야 한다. 위에 있으면 /ui/*
        까지 삼켜서 API 가 전부 HTML 을 돌려준다. 그래서 파일 끝이다.
        """
        # 실제 파일이면 그대로 준다 (favicon.png, vite.svg 등)
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")

    print(f"  🌐 프론트 서빙: {_DIST}")
