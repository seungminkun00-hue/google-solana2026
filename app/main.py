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
import secrets
import uuid

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from app.agents import pipeline
from app.bots import BOTS, DEMO_BOT_IDS, BotInstance, make_demo_bots
from app.core.ledger import LEDGER, InsufficientFunds, RouteViolation
from app.core.mandate import issue_invoice
from app.core.proofs import PROOFS
from app.core.positions import BOOK, Position, should_close
from app.core.receipts import RECEIPTS
from app.core.scheduler import SCHEDULER
from app.core import store as STORE
from app.core.x402_client import (BudgetExceeded, KillSwitchActive,
                                  PaymentFailed, paid_fetch)
from app.core.x402_provider import paywall_dynamic
from app.external import router as mock_router, spot
from app.models import Rulebook, Signal, Thesis
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
make_demo_bots()

# 저장된 상태를 되살린다. make_demo_bots() 뒤여야 한다 —
# 데모봇 정의는 코드가, 사용자 봇은 파일이 진실원이다.
_restored = STORE.load()
if _restored["bots"]:
    print(f"  ↺ 상태 복원: 봇 {_restored['bots']}개"
          f"(신규 {len(_restored['restored_bots'])}) "
          f"영수증 {_restored['receipts']} 포지션 {_restored['positions']}")

SIGNALS: dict[str, Signal] = {}
THESES: dict[str, Thesis] = {}
BASE = "http://testserver"


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
            "hint": "선언한 추론과 실제 추론이 다릅니다."
                    if receipt.degraded else "BYOK 영수증입니다."})

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
    bot = get_bot(bot_id)
    log: list[dict] = []
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

        # 외부 에이전트의 테제 구매 (모의)
        ext = await LEDGER.transfer("external", bot.w("revenue-wallet"),
                              config.PRICE_THESIS, f"ext-buy:{thesis.thesis_id}")
        log.append({"step": "external-sale", "amount": ext.amount})

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
                r = await c.post(f"{BASE}/settle/{pos.receipt_id}")
            entry["closed"] = True
            entry["settle"] = r.json() if r.status_code == 200 else r.text[:150]
        actions.append(entry)
    return {"bot_id": bot_id, "open_positions": len(BOOK.of_bot(bot_id)),
            "actions": actions}


# ── 정산 ────────────────────────────────────────────────────────────
@app.post("/settle/{receipt_id}")
async def settle(receipt_id: str, _: None = Depends(require_operator)):
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
    result.update({"close_tx": proof.proof_id,
                   "entry_price": pos.entry_price / 10**6,
                   "exit_price": exit_price / 10**6,
                   "qty": pos.qty / 10**6,
                   "held_hours": round(pos.held_hours(), 2)})
    BOOK.close(receipt_id)
    STORE.save()
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
async def create_bot(req: CreateBotRequest, _: None = Depends(require_admin)):
    """앱에서 봇을 생성한다."""
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
    bot = BotInstance(bot_id=bot_id, owner=req.owner, rulebook=rb)

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
    STORE.save()
    return {"deleted": bot_id}


@app.post("/bots/{bot_id}/close-all")
async def close_all(bot_id: str, _: None = Depends(require_operator)):
    """이 봇의 모든 포지션을 즉시 청산한다 (룰북 조건 무시).

    사용자가 "지금 다 팔아"라고 할 때 쓰는 비상 탈출구.
    """
    get_bot(bot_id)
    results = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 timeout=120, headers=INTERNAL_HEADERS) as c:
        for pos in BOOK.of_bot(bot_id):
            r = await c.post(f"{BASE}/settle/{pos.receipt_id}")
            results.append(r.json() if r.status_code == 200 else r.text[:120])
    return {"closed": len(results), "results": results}


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
