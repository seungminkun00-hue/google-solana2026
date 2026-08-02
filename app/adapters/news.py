"""종목 뉴스 — Alpha Vantage NEWS_SENTIMENT.

    GET https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers=AAPL

[왜 필요한가]
"애플 요즘 어때?", "호재 있어?" 같은 질문에 원장 숫자로 답하면 그건 답이
아니다. 봇이 자기 잔고만 읊는 이유는 그것밖에 못 봤기 때문이고, 그러면
사용자 눈에는 그냥 바보다. 실제 기사를 읽혀야 실제로 답할 수 있다.

[왜 Gemini 검색 그라운딩이 아닌가]
이 프로젝트의 Gemini 키는 free tier 라 `google_search` 도구가 quota 0 이다
(2026-08-02 실측: 같은 모델에 일반 요청 200, 검색 도구 붙이면 429).
그래서 검색은 우리가 하고, 읽고 판단하는 것만 Gemini 가 한다.

[한도 — 아껴 써야 한다]
Alpha Vantage 무료 한도는 **하루 25회**다. 사이클마다 부르면 몇 분 만에
소진된다. 그래서
  · 종목당 캐시 TTL 10분
  · 실패하면 마지막으로 받아둔 것을 계속 쓴다 (오래됐다는 표시와 함께)
  · 대화에서 사용자가 그 종목을 물었을 때만 부른다. 매매 사이클은
    부르지 않는다 — 사이클은 이미 자체 뉴스 라우트를 x402 로 결제한다.
"""
from __future__ import annotations

import os
import time

import httpx

BASE = "https://www.alphavantage.co/query"
TTL = 600                     # 10분
TIMEOUT = 20
MAX_ITEMS = 6

# ticker -> (받은 시각, 기사 목록)
_CACHE: dict[str, tuple[float, list[dict]]] = {}


def enabled() -> bool:
    """뉴스를 받아올 수 있는가.

    ⚠️ 2026-08-02 실측: 이 엔드포인트는 **키 없이도 응답한다**(잘못된 키를
    보내도 같은 데이터가 온다). 그래서 기본값은 '켜짐'이고, 키가 있으면
    같이 보낸다. 다만 이건 우리가 통제하는 성질이 아니다 —
    Alpha Vantage 가 언제든 키를 요구하기 시작할 수 있고, 그때는 이 함수가
    아니라 headlines() 가 빈 목록을 돌려주며 조용히 내려간다.
    무료 한도는 IP 기준 하루 25회로 보인다.

    NEWS_OFF=1 로 끌 수 있다 — 한도를 아껴야 하는 시연 직전에 쓴다.
    """
    return os.environ.get("NEWS_OFF", "0") != "1"


def _normalize(ticker: str) -> str:
    """미러 토큰 표기(NVDAx) → 실제 티커(NVDA)."""
    return ticker[:-1] if ticker.endswith("x") else ticker


async def headlines(ticker: str) -> list[dict]:
    """이 종목의 최근 기사. 실패하면 빈 목록 — 예외를 밖으로 내지 않는다.

    대화가 뉴스를 못 받았다고 죽으면 안 된다. 못 받았으면 봇이
    "지금은 뉴스를 못 봤다"고 말하는 편이 낫다.
    """
    sym = _normalize(ticker).upper()
    hit = _CACHE.get(sym)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    if not enabled():
        return hit[1] if hit else []

    # 키가 없으면 빈 값으로 보낸다. 지금은 그래도 응답하지만, 요구하기
    # 시작하면 아래 feed 검사에서 걸러져 조용히 빈 목록이 된다.
    key = os.environ.get("ALPHAVANTAGE_API_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(BASE, params={
                "function": "NEWS_SENTIMENT", "tickers": sym,
                "limit": 20, "apikey": key})
        data = r.json()
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 뉴스 조회 실패({sym}): {str(e)[:90]}")
        return hit[1] if hit else []

    feed = data.get("feed")
    if not isinstance(feed, list):
        # 한도 초과 메시지는 {"Information": "..."} 로 온다. 조용히 넘기면
        # 왜 뉴스가 안 나오는지 알 수 없다.
        note = data.get("Information") or data.get("Note") or data.get("Error Message")
        if note:
            print(f"  ⚠️ 뉴스 API 응답({sym}): {str(note)[:120]}")
        return hit[1] if hit else []

    out = []
    for a in feed[:MAX_ITEMS]:
        # 이 종목에 대한 감성만 뽑는다. 전체 감성은 다른 종목이 섞여 있다.
        mine = next((t for t in a.get("ticker_sentiment", [])
                     if t.get("ticker") == sym), None)
        out.append({
            "title": a.get("title", "")[:200],
            "source": a.get("source", ""),
            "published": a.get("time_published", "")[:13],   # YYYYMMDDTHH
            "url": a.get("url", ""),
            "summary": (a.get("summary") or "")[:300],
            "sentiment": (mine or {}).get("ticker_sentiment_label")
                         or a.get("overall_sentiment_label", ""),
            "relevance": (mine or {}).get("relevance_score", ""),
        })
    _CACHE[sym] = (time.time(), out)
    return out


def as_text(ticker: str, items: list[dict]) -> str:
    """프롬프트에 넣을 모양. 제목·출처·시각·감성만 — 요약까지 넣으면
    프롬프트가 길어져 응답이 느려지고, 판단에 필요한 건 제목 쪽이다."""
    if not items:
        return f"{_normalize(ticker)} 관련 최근 기사를 받아오지 못했습니다."
    lines = [f"{_normalize(ticker)} 최근 기사:"]
    for a in items:
        when = a["published"].replace("T", " ")
        lines.append(f"  · [{when}] {a['title']} "
                     f"({a['source']}, 감성 {a['sentiment'] or '미상'})")
    return "\n".join(lines)
