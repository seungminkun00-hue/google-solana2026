"""세 에이전트 (Managed v2 — 봇 인스턴스 기반).

전역 싱글톤이 사라졌다. 모든 함수가 bot: BotInstance를 받는다.
should_think의 입력이 상수가 아니라 RECEIPTS.stats(bot_id)에서 온다 —
기대값이 상상이 아니라 정산된 영수증에서 나온다.
"""
from __future__ import annotations

import httpx

from app import config
from app.bots import BotInstance
from app.core.ledger import LEDGER
from app.core.mandate import issue_invoice
from app.core.positions import BOOK
from app.core.receipts import RECEIPTS
from app.core.x402_client import paid_fetch
from app.pricing import deep_cost
from app.inference import route
from app.models import Signal, Thesis


class PolicyRejected(Exception):
    pass


# ── MVoT: 산수 문지기 (LLM 아님, 0원) ───────────────────────────────
def should_think(bot: BotInstance, novelty: float, relevance: float,
                 headline: str = "") -> tuple[str, dict]:
    stats = RECEIPTS.stats(bot.bot_id)
    # 프롬프트 길이로 실제 비용을 추정한다. 고정 상수보다 정확하고,
    # 긴 뉴스일수록 비싸다는 실제 과금 구조를 반영한다.
    est_cost = (deep_cost(headline) if headline else config.PRICE_GEMINI_DEEP) \
               + config.PRICE_MARKET_DATA
    est_value = int(novelty * relevance * stats["hit_rate"]
                    * bot.rulebook.max_position_usdc * stats["avg_return"])
    detail = {"est_cost": est_cost, "est_value": est_value,
              "min_required": est_cost * config.MIN_ROI,
              "hit_rate": stats["hit_rate"], "cold_start": stats["cold_start"]}
    if est_value < est_cost * config.MIN_ROI:
        return "SKIP", detail
    remaining = bot.tracker.policy.daily_cap - bot.tracker.spent_today
    if est_cost > remaining:
        return "DEFER", detail
    return "THINK", detail


# ── Scout: 뉴스 → Flash 스크리닝 → MVoT ─────────────────────────────
async def scout_run(client: httpx.AsyncClient, base: str,
                    bot: BotInstance) -> tuple[Signal | None, dict]:
    bot.tracker.begin_decision()
    news, p1 = await paid_fetch(client, route("exa_search", base), bot.tracker)

    # 룰북 사전 필터 — 허용 목록 밖 종목이면 Flash조차 안 부른다.
    # 헌법이 지갑을 지키는 첫 지점: 여기서 걸리면 지출은 뉴스값 2원뿐.
    if news["ticker"] not in bot.rulebook.allowed_tickers:
        return None, {"verdict": "RULEBOOK_SKIP", "ticker": news["ticker"],
                      "headline": news["headline"],
                      "_proofs": [p for p in (p1,) if p]}

    # 2단계 모델 1층: 싼 Flash로 관련성만 채점 (Deep의 1/12 가격)
    flash, p2 = await paid_fetch(client, route("gemini_flash", base),
                                 bot.tracker, {"text": news["headline"]})
    relevance = flash["relevance"]

    verdict, detail = should_think(bot, news["novelty"], relevance,
                                   headline=news["headline"])
    meta = {"verdict": verdict, **detail, "headline": news["headline"],
            "relevance": relevance,
            "_proofs": [p for p in (p1, p2) if p],
            # 실제로 무엇이 점수를 매겼는지. 선언 모드가 아니라 이게
            # 영수증에 박힌다. Gemini가 실패해 폴백하면 "mock"이 온다.
            "_sources": {"flash": flash.get("source", "mock")}}
    if verdict != "THINK":
        return None, meta

    sig = Signal(bot_id=bot.bot_id, ticker=news["ticker"], headline=news["headline"],
                 novelty=news["novelty"], relevance=relevance, source_hash=news["url"])
    return sig, meta


# ── Analyst: 시그널 구매 → Deep 추론 → 테제 ─────────────────────────
async def analyst_run(client: httpx.AsyncClient, base: str, bot: BotInstance,
                      signal_id: str, seller_bot_id: str,
                      scout_proofs: list | None = None,
                      scout_sources: dict | None = None) -> Thesis:
    if scout_proofs is None:          # 봇 간 구매 = 새로운 의사결정
        bot.tracker.begin_decision()
    collected = list(scout_proofs or [])
    sources = dict(scout_sources or {})

    # 시그널 구매 — 자기 봇 것이든 남의 봇 것이든 똑같이 돈을 낸다
    sig_data, p_sig = await paid_fetch(
        client, f"{base}/bots/{seller_bot_id}/sell/signal/{signal_id}", bot.tracker)
    deep, p_deep = await paid_fetch(
        client, route("gemini_deep", base), bot.tracker,
        {"novelty": sig_data["novelty"], "ticker": sig_data["ticker"],
         "headline": sig_data["headline"]})
    quote, p_q = await paid_fetch(client, route("market_quote", base),
                                  bot.tracker, {"ticker": sig_data["ticker"]})
    collected += [p for p in (p_sig, p_deep, p_q) if p]
    sources["deep"] = deep.get("source", "mock")

    receipt = RECEIPTS.create(
        bot_id=bot.bot_id,
        source_urls=[sig_data["source_hash"]],
        prompt=f"deep:{sig_data['headline']}",
        output=str(deep),
        proofs=collected,
        policy_snapshot={"rulebook": bot.rulebook.model_dump(mode="json"),
                         "spend": bot.tracker.policy.model_dump()},
        splits_bps=bot.splits,                    # 분배표 박제
        declared_mode=config.INFERENCE_MODE,      # 쓰려고 했던 것
        inference_sources=sources,                # 실제로 답한 것
    )
    size = int(bot.rulebook.max_position_usdc * deep["confidence"] * 0.6)
    # 시세를 테제에 실어 보낸다. 체결 수량 계산과 손익 산출의 기준이 된다.
    return Thesis(bot_id=bot.bot_id, ticker=sig_data["ticker"], side=deep["side"],
                  confidence=deep["confidence"], rationale=deep["rationale"],
                  size_usdc=size, price_micro=int(quote["price_micro_usdc"]),
                  receipt_id=receipt.receipt_id)


# ── Executor: 룰북 게이트 → 자본 청구 → 체결 ────────────────────────
def rulebook_gate(bot: BotInstance, thesis: Thesis) -> dict:
    """LLM이 뭐라고 하든 최종 거부권. 헌법은 코드다."""
    rb = bot.rulebook
    if bot.killed:
        raise PolicyRejected("킬 스위치 활성")
    if thesis.ticker not in rb.allowed_tickers:
        raise PolicyRejected(f"룰북 위반: {thesis.ticker}는 허용 목록에 없음")
    if thesis.confidence < rb.min_confidence:
        raise PolicyRejected(f"확신도 미달: {thesis.confidence} < {rb.min_confidence}")
    if thesis.size_usdc and thesis.size_usdc > rb.max_position_usdc:
        raise PolicyRejected("포지션 상한 초과")
    if bot.trades_today >= rb.max_trades_per_day:
        raise PolicyRejected("일일 거래 한도 도달")
    return {"passed": True}


async def request_capital(bot: BotInstance, thesis: Thesis) -> dict | None:
    """투자지갑 잔고가 부족하면 트레저리에 청구한다.
    두 번째 '결제 요청 생성·입금' — 성과가 나쁘면 거절된다."""
    balance = (await LEDGER.snapshot()).get(bot.w("invest-wallet"), 0)
    if thesis.size_usdc <= balance:
        return None
    stats = RECEIPTS.stats(bot.bot_id)
    inv = issue_invoice(
        kind="capital",
        issuer=bot.w("invest-wallet"),
        payer=bot.w("user-treasury"),
        balance=balance,
        needed=thesis.size_usdc,
        expected_value=int(thesis.size_usdc * thesis.confidence
                           * stats["avg_return"] * 10),
        reason=f"{thesis.ticker} 확신도 {thesis.confidence} 기회 — "
               f"잔고 {balance}, 필요 {thesis.size_usdc}",
    )
    # [교정 2026-08] 예전에는 invest-wallet '잔고'를 노출로 넘겼다.
    # 잔고는 아직 시장에 안 나간 유휴 현금이라 노출이 아니다. 그 결과
    # CAP_MANDATE_MAX_EXPOSURE 검사가 사실상 무의미했다 — 포지션을
    # 아무리 많이 열어도 잔고만 적으면 통과했다.
    # 실제 노출은 '열린 포지션의 원가 합'이고, 그걸 계산하는
    # BOOK.exposure()가 이미 있었는데 아무도 부르지 않고 있었다.
    # 요청액(inv.amount)을 더하는 것은 만다트 쪽이 한다.
    result = await bot.cap_mandate.process(
        inv, hit_rate=stats["hit_rate"], cold_start=stats["cold_start"],
        current_exposure=BOOK.exposure(bot.bot_id))
    return result.model_dump()
