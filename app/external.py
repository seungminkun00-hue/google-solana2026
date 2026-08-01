"""모의 외부 세계.

Exa / Gemini / 시장데이터를 같은 프로세스 안의 x402 페이월 라우트로
구현한다. 덕분에 데모가 네트워크 없이 완주된다.

실전 전환: 결제 경로는 X402_MODE가 고른다 (app/inference.py 참조).
추론 백엔드는 INFERENCE_MODE가 따로 고른다 — 두 축은 독립이다.

체결은 미러 주식 토큰 스왑(LEDGER.swap_in/swap_out)이 담당한다.
국내 자본시장법상 미정리 영역이므로 데모는 devnet 미러 토큰이고,
실체결 경로는 어댑터 교체로만 활성화한다.
"""
from __future__ import annotations

import hashlib
import random

from fastapi import APIRouter, Depends

from app import config
from app.core.x402_provider import paywall

router = APIRouter(prefix="/mock", tags=["mock-external"])

# 헤드라인은 아직 모의값이지만, 티커와 회사명은 smartmoney.market의
# 실제 SEC 추적 목록(1,045종목)에서 검증된 것이다.
_NEWS = [
    ("NVDAx", "Nvidia Corp — 차세대 추론칩 수요 예상치 상회 보고"),
    ("TSLAx", "Tesla Inc — 에너지 저장 부문 마진 개선 발표"),
    ("MSFTx", "Microsoft Corp — 클라우드 부문 성장률 반등"),
    ("AAPLx", "Apple Inc — 서비스 매출 성장 둔화 신호"),
]


# ── Exa: 뉴스 검색 ($0.002) ─────────────────────────────────────────
@router.post("/exa/search")
async def exa_search(_: str = Depends(paywall(config.PRICE_EXA_SEARCH, "external"))):
    ticker, headline = random.choice(_NEWS)
    url = f"https://news.example/{hashlib.sha256(headline.encode()).hexdigest()[:8]}"
    return {"ticker": ticker, "headline": headline, "url": url,
            "novelty": round(random.uniform(0.2, 0.95), 2)}


# ── Gemini: 심층추론 ────────────────────────────────────────────────
@router.post("/gemini/deep")
async def gemini_deep(payload: dict = {},
                      _: str = Depends(paywall(config.PRICE_GEMINI_DEEP, "external"))):
    """심층 추론 — 매매 방향·확신도·근거.

    INFERENCE_MODE=managed 이면 실제 Gemini Pro에 mainnet 결제로 물어본다.
    """
    novelty = float(payload.get("novelty", 0.5))
    from app.adapters import gemini_live
    if gemini_live.enabled():
        try:
            out = await gemini_live.analyze_async(
                payload.get("ticker", ""), payload.get("headline", ""))
            return {**out, "source": "gemini-live"}
        except Exception as e:
            print(f"  ⚠️ Gemini Pro 실패: {str(e)[:90]} → 모의 판단 사용")
    side = "buy" if novelty >= 0.5 else "sell"
    confidence = round(min(0.95, 0.4 + novelty * 0.6), 2)
    return {"side": side, "confidence": confidence, "source": "mock",
            "rationale": f"신규성 {novelty} 기반 {side} 판단. 촉매의 지속성이 관건."}


# ── 가격 오라클 ─────────────────────────────────────────────────────
# 모의 시장의 기준가. 유료 라우트와 정산 시점 평가가 같은 출처를 쓴다.
# 실전에서는 pay.sh 시장데이터 라우트로 교체된다.
_BASE_PRICES = {"NVDAx": 131_000_000, "TSLAx": 244_000_000,
                "MSFTx": 421_000_000, "AAPLx": 229_000_000}


async def spot(ticker: str) -> int:
    """현재가 (마이크로 USDC).

    PRICE_SOURCE=live 이면 pay.sh MPP 라우트에서 402 결제로 받아온다.
    실패하거나 mock 모드면 내장 기준가 ±3%를 쓴다.

    매수 시점과 청산 시점에 각각 호출되므로, 손익이 난수가 아니라
    '두 시점의 가격 차이'에서 자연스럽게 나온다.

    [async로 바꾼 이유 — 2026-08]
    live 모드에서 이 함수는 `pay` CLI 서브프로세스를 최대 40초 기다린다.
    동기 호출이라 그동안 이벤트 루프가 통째로 멈췄다 — 스케줄러도,
    다른 봇의 사이클도, 대시보드 조회도 전부 정지한다.
    live_quotes.spot_live_async 가 이미 있었는데 아무도 쓰지 않았다.
    호출부는 전부 async 컨텍스트라 await만 붙이면 된다.
    """
    from app.adapters import live_quotes
    if live_quotes.available():
        try:
            return await live_quotes.spot_live_async(ticker)
        except live_quotes.QuoteUnavailable as e:
            # 시세를 못 받았다고 매매를 멈추면 데모가 통째로 죽는다.
            # 내장 기준가로 내려가되, 무슨 일이 있었는지는 남긴다.
            print(f"  ⚠️ 실전 시세 실패({ticker}): {e} → 내장 기준가 사용")
    base = _BASE_PRICES.get(ticker, 100_000_000)
    return int(base * random.uniform(0.97, 1.03))


# ── 시장데이터: 가격 조회 ($0.004) ──────────────────────────────────
@router.post("/market/quote")
async def market_quote(payload: dict = {},
                       _: str = Depends(paywall(config.PRICE_MARKET_DATA, "external"))):
    t = payload.get("ticker", "MSFTx")
    return {"ticker": t, "price_micro_usdc": await spot(t)}


# [삭제됨 2026-08] PaperJupiter — 손익을 난수로 만들던 페이퍼 스왑.
# 미러 주식 토큰 스왑(LEDGER.swap_in/swap_out)으로 완전히 대체됐다.
# 이제 손익은 난수가 아니라 진입가와 청산가의 차이에서 나온다.
# 참조가 0이라 삭제했다 — 폴백 경로로도 쓰이지 않았음을 확인했다.


# ── Gemini Flash: 1차 스크리닝 — 2단계 모델의 문지기 ────────────────
@router.post("/gemini/flash")
async def gemini_flash(payload: dict = {},
                       _: str = Depends(paywall(config.PRICE_GEMINI_FLASH, "external"))):
    """싼 모델로 '이 뉴스가 투자 관련성이 있나'만 빠르게 채점.
    통과분만 훨씬 비싼 Pro로 간다.

    INFERENCE_MODE=managed 이면 실제 Gemini에 mainnet 결제로 물어본다.
    실패하면 모의 점수로 폴백 — 추론 하나 못 받았다고 데모가 죽으면 안 된다.
    """
    text = payload.get("text", "")
    from app.adapters import gemini_live
    if gemini_live.enabled():
        try:
            return {"relevance": round(await gemini_live.screen_async(text), 2),
                    "source": "gemini-live"}
        except Exception as e:
            print(f"  ⚠️ Gemini Flash 실패: {str(e)[:90]} → 모의 점수 사용")
    relevance = min(0.95, 0.3 + len(text) % 7 / 10)
    return {"relevance": round(relevance, 2), "source": "mock"}
