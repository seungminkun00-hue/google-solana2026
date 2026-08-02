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
import os
import random
from contextlib import contextmanager
from contextvars import ContextVar

from fastapi import APIRouter, Depends

from app import config
from app.core.x402_provider import paywall

router = APIRouter(prefix="/mock", tags=["mock-external"])

# ── 시연용 뉴스 보장 (2026-08) ──────────────────────────────────────
#
# [무엇을 하고 무엇을 하지 않는가]
# 하는 것: 뉴스의 **티커**와 **신규성·관련성 점수**를 시연에 맞게 고정한다.
# 하지 않는 것: 판단·룰북·만다트·체결은 전혀 손대지 않는다.
#
# 심사위원 앞에서 뉴스가 난수로 뽑히면 "허용목록 밖 종목" 이나
# "신규성 0.24 → 생각할 가치 없음" 으로 끝나 시연이 죽는다. 그렇다고
# 판단을 건너뛰면 '에이전트가 스스로 결정한다'는 주장 자체가 사라진다.
# 그래서 재료만 보장하고 결정은 그대로 시킨다 — "좋은 뉴스가 왔고,
# 봇이 그걸 보고 샀다"가 사실로 남는다.
#
# ContextVar 인 이유: 스케줄러가 다른 봇의 사이클을 동시에 돌릴 수 있다.
# 모듈 전역이면 그쪽 뉴스까지 같이 편향된다. ASGITransport 는 같은 태스크
# 안에서 앱을 부르므로 컨텍스트가 그대로 전파된다.
_DEMO_BIAS: ContextVar[dict | None] = ContextVar("demo_bias", default=None)


@contextmanager
def demo_bias(tickers, min_novelty: float = 0.85, min_relevance: float = 0.9):
    """이 블록 안에서 오는 뉴스만 편향시킨다."""
    token = _DEMO_BIAS.set({"tickers": list(tickers),
                            "min_novelty": min_novelty,
                            "min_relevance": min_relevance})
    try:
        yield
    finally:
        _DEMO_BIAS.reset(token)

# ⚠️ **헤드라인은 모의값이다.** 티커와 회사명만 실제다 — 국내·일본은 KIS 가
#    확인해 준 이름이고, 미국은 smartmoney.market 의 SEC 추적 목록이다.
#
# [왜 생성하나 — 2026-08-03]
# 예전에는 미국 4종의 헤드라인만 손으로 적어 뒀다. 시장을 코스피·코스닥·
# 도쿄까지 늘리자 국내 종목 뉴스가 하나도 없어서, 코스피 봇은 정찰에서
# 매번 "허용목록 밖 종목" 으로 끝났다 — 살 수 있는데 살 기회가 안 왔다.
# 이제 거래 가능한 전 종목에 대해 만든다.
#
# 실제 기사는 대화 쪽(app/adapters/news.py)이 따로 받아온다. 사이클의
# 뉴스는 x402 결제 대상이라 모의 라우트로 두는 것이 이 데모의 구조다.
_CURATED = {
    "NVDAx": "차세대 추론칩 수요 예상치 상회 보고",
    "TSLAx": "에너지 저장 부문 마진 개선 발표",
    "MSFTx": "클라우드 부문 성장률 반등",
    "AAPLx": "서비스 매출 성장 둔화 신호",
}

_TEMPLATES = [
    "분기 실적 시장 기대치 상회",
    "주력 사업 수요 회복 신호",
    "증권가 목표주가 상향 조정",
    "설비 투자 확대 계획 발표",
    "경쟁 심화에 따른 마진 압박 우려",
]


def _news_pool() -> list[tuple[str, str]]:
    """거래 가능한 전 종목의 (미러티커, 헤드라인).

    카탈로그가 비어 있으면(검증 파일 없음) 손으로 적어둔 4종으로 돌아간다.
    """
    from app.core.markets import MARKETS, company
    out: list[tuple[str, str]] = []
    for m in MARKETS:
        for i, t in enumerate(m["tickers"]):
            mt = f"{t}x"
            head = _CURATED.get(mt) or _TEMPLATES[i % len(_TEMPLATES)]
            out.append((mt, f"{company(t)} — {head}"))
    return out or [(k, f"{k[:-1]} — {v}") for k, v in _CURATED.items()]


# ── Exa: 뉴스 검색 ($0.002) ─────────────────────────────────────────
@router.post("/exa/search")
async def exa_search(payload: dict = {},
                     _: str = Depends(paywall(config.PRICE_EXA_SEARCH, "external"))):
    """뉴스 한 건.

    payload.tickers 로 **찾을 범위**를 좁힐 수 있다. 실제 Exa 검색도
    질의를 종목으로 한정해 던지므로, 봇이 자기 룰북 범위의 뉴스를 사는
    것이 자연스럽다.

    [왜 필요해졌나 — 2026-08-03]
    시장을 넓혀 종목이 76개가 되자, 코스피 봇이 도쿄 종목 뉴스를 받아
    "허용목록 밖" 으로 버리는 일이 대부분이 됐다. 정찰을 5번 돌려도
    자기 종목이 안 걸려 매번 빈손으로 끝났다 — 돈만 쓰고 기회는 없다.

    범위를 좁히는 것과 판단을 대신하는 것은 다르다. 무엇을 살지,
    확신도를 얼마로 볼지는 여전히 추론이 정한다.
    """
    all_news = _news_pool()
    scope = [str(t) for t in (payload.get("tickers") or [])]
    if scope:
        mine = [n for n in all_news if n[0] in scope]
        if mine:
            all_news = mine
    pool, lo = all_news, 0.2
    bias = _DEMO_BIAS.get()
    if bias:
        # 룰북 허용 종목의 뉴스만 남긴다. 하나도 없으면 편향을 포기하고
        # 원래대로 뽑는다 — 빈 목록에서 고르면 여기서 죽는다.
        picked = [n for n in all_news if n[0] in bias["tickers"]]
        if picked:
            pool, lo = picked, bias["min_novelty"]

    ticker, headline = random.choice(pool)
    url = f"https://news.example/{hashlib.sha256(headline.encode()).hexdigest()[:8]}"
    return {"ticker": ticker, "headline": headline, "url": url,
            "novelty": round(random.uniform(lo, 0.95), 2)}


# ── Gemini: 심층추론 ────────────────────────────────────────────────
def _bot_prompt(bot_id: str) -> tuple[str, str]:
    """이 봇의 심층추론 시스템 프롬프트와 선택된 모델 ID.

    봇을 못 찾으면 빈 값 — 그때는 어댑터의 기본 지침으로 간다.
    payload 로 bot_id 를 받는 이유는, 페이월 라우트가 결제자만 알 뿐
    '어느 봇의 지침으로 판단해야 하는지'는 모르기 때문이다.
    """
    if not bot_id:
        return "", ""
    try:
        from app.bots import BOTS
        from app.core.profiles import PROFILES, model_id
        from app.core.prompts import trading_system_prompt
        bot = BOTS.get(bot_id)
        if bot is None:
            return "", ""
        prof = PROFILES.get(bot_id)
        return trading_system_prompt(bot, prof), model_id(prof.model if prof else "")
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 봇 지침 조립 실패({bot_id}): {str(e)[:80]}")
        return "", ""


@router.post("/gemini/deep")
async def gemini_deep(payload: dict = {},
                      _: str = Depends(paywall(config.PRICE_GEMINI_DEEP, "external"))):
    """심층 추론 — 매매 방향·확신도·근거.

    INFERENCE_MODE=byok    이면 사용자 API 키로 Gemini에 직접 물어본다.
    INFERENCE_MODE=managed 이면 pay.sh 게이트웨이에 mainnet 결제로 물어본다.

    어느 쪽이든 실패하면 모의 판단으로 내려간다. 추론 하나 못 받았다고
    시연이 통째로 죽으면 안 되고, 폴백했다는 사실은 응답의 source 와
    영수증의 degraded 로 그대로 드러난다.
    """
    novelty = float(payload.get("novelty", 0.5))
    from app.adapters import gemini_byok, gemini_live
    if gemini_byok.enabled():
        from app.core.prompts import display_ticker
        system, model = _bot_prompt(str(payload.get("bot_id", "")))
        try:
            out = await gemini_byok.analyze(
                # 룰북 목록과 같은 표기로 보낸다. 한쪽만 미러 토큰 접미사를
                # 달고 있으면 모델이 다른 종목으로 읽는다.
                display_ticker(str(payload.get("ticker", ""))),
                payload.get("headline", ""),
                system=system, model=model)
            return {"side": out["side"], "confidence": out["confidence"],
                    "rationale": out["rationale"], "source": out["model"]}
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠️ Gemini 심층추론 실패: {str(e)[:90]} → 모의 판단 사용")
    elif gemini_live.enabled():
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
    source = os.environ.get("PRICE_SOURCE", "kis").lower()

    # ① 한국투자증권(KIS) 실시세. 국내·해외를 한 키로 준다.
    #    국내 종목은 원화로 오므로 환율로 USD 환산한다 — 원장의 기준
    #    통화가 USDC 라서, 여기서 안 바꾸면 262,500 이 $262,500 이 된다.
    #
    #    ⚠️ PRICE_SOURCE=mock 이면 여기로 오지 않는다. 검증 스크립트가
    #       손절·익절을 증명하려면 기준가를 직접 움직여야 하는데, 실시세를
    #       쓰면 그 조작이 무시돼 "가격을 -15% 내렸는데 손절이 안 걸린다"가
    #       된다(실측). 시장을 통제할 수 없으면 청산 규칙을 증명할 수 없다.
    from app.adapters import kis_quotes
    from app.core.markets import quote_spec
    spec = quote_spec(ticker)
    if source == "kis" and kis_quotes.enabled() and spec is not None:
        try:
            value, currency = await kis_quotes.price(ticker, **spec)
            # 거래소 통화 → USD. 원장의 기준 통화가 USDC 라서 들어오는
            # 지점에서 반드시 환산한다 — 안 하면 토요타 3,067엔이 $3,067,
            # 삼성전자 262,500원이 $262,500 이 된다.
            if currency != "USD":
                from app.adapters import fx as FX
                value = value * await FX.rate_to_usd(currency)
            micro = int(value * 1_000_000)
            if micro > 0:
                return micro
            print(f"  ⚠️ KIS 시세가 0 이하({ticker}) → 다음 경로 사용")
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠️ KIS 시세 실패({ticker}): {str(e)[:110]} → 다음 경로 사용")

    # ② pay.sh MPP 라우트 (PRICE_SOURCE=live 일 때만)
    from app.adapters import live_quotes
    if source == "live" and live_quotes.available():
        try:
            return await live_quotes.spot_live_async(ticker)
        except live_quotes.QuoteUnavailable as e:
            # 시세를 못 받았다고 매매를 멈추면 데모가 통째로 죽는다.
            # 내장 기준가로 내려가되, 무슨 일이 있었는지는 남긴다.
            print(f"  ⚠️ 실전 시세 실패({ticker}): {e} → 내장 기준가 사용")

    # ③ 내장 기준가. 여기까지 오면 실시세가 아니라는 뜻이다.
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
    from app.adapters import gemini_byok, gemini_live
    if gemini_byok.enabled():
        try:
            score, model = await gemini_byok.screen(text)
            return {"relevance": round(score, 2), "source": model}
        except Exception as e:                            # noqa: BLE001
            print(f"  ⚠️ Gemini 스크리닝 실패: {str(e)[:90]} → 모의 점수 사용")
    elif gemini_live.enabled():
        try:
            return {"relevance": round(await gemini_live.screen_async(text), 2),
                    "source": "gemini-live"}
        except Exception as e:
            print(f"  ⚠️ Gemini Flash 실패: {str(e)[:90]} → 모의 점수 사용")
    relevance = min(0.95, 0.3 + len(text) % 7 / 10)
    bias = _DEMO_BIAS.get()
    if bias:
        # 헤드라인 길이로 정해지는 값이라 짧은 뉴스는 0.3 까지 떨어진다.
        # 그러면 MVoT 가 '생각할 가치 없음' 으로 끊는다. 시연에서는
        # 바닥만 올린다 — 점수를 매기는 행위 자체는 그대로 일어난다.
        relevance = max(relevance, bias["min_relevance"])
    return {"relevance": round(relevance, 2), "source": "mock"}
