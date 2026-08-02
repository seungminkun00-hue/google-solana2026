"""환율 어댑터 — 원장은 USDC로 돌고, 화면은 원화로 읽힌다.

[왜 어댑터인가]
이 시스템의 진실원은 마이크로 USDC 정수다. 원화는 **표시 단위**일 뿐이고,
어디에서도 원화로 계산하지 않는다. 환율이 바뀌어도 잔고·손익·정산은
1µUSDC도 달라지지 않는다. 곱하는 지점을 한 곳으로 모아두는 이유다.

[출처] open.er-api.com — API 키가 필요 없는 무료 라우트.
       하루 한 번 갱신되므로 캐시 1시간이면 충분하다.

[실패했을 때] 환율을 못 받았다고 앱이 죽으면 안 된다. 마지막으로 성공한
       값을 쓰고, 그것도 없으면 FX_USD_KRW 기본값으로 내려간다.
       응답에는 항상 출처(source)가 실려서, 화면이 '실시간'인지
       '고정값'인지 숨기지 않는다.
"""
from __future__ import annotations

import os
import time

import httpx

URL = "https://open.er-api.com/v6/latest/USD"
CACHE_TTL = 3_600.0
TIMEOUT = 8.0

# 네트워크가 없을 때 쓰는 최후의 값. 발표 당일 와이파이가 끊겨도
# 화면에 0원이 뜨지 않게 하는 용도이지, 정확한 값이 아니다.
FALLBACK = float(os.environ.get("FX_USD_KRW", "1400"))

# rates 는 전 통화 표(USD 기준). 해외 시세 환산에 쓴다 —
# 같은 응답에 들어 있으므로 호출을 더 하지 않는다.
_cache: dict = {"rate": None, "ts": 0.0, "source": "fallback",
                "updated": None, "rates": {}}


async def usd_krw() -> dict:
    """USD→KRW. {"rate": float, "source": str, "updated": str|None}"""
    now = time.time()
    if _cache["rate"] is not None and now - _cache["ts"] < CACHE_TTL:
        return {k: v for k, v in _cache.items() if k != "rates"}

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(URL)
            r.raise_for_status()
            data = r.json()
        rate = float(data["rates"]["KRW"])
        if rate <= 0:
            raise ValueError(f"환율이 0 이하: {rate}")
        _cache.update(rate=rate, ts=now, source="open.er-api.com",
                      updated=data.get("time_last_update_utc"),
                      rates=data.get("rates") or {})
    except Exception as e:                            # noqa: BLE001
        # 이전 값이 있으면 그걸 계속 쓴다. 만료됐어도 없는 것보다 낫다.
        if _cache["rate"] is None:
            print(f"  ⚠️ 환율 조회 실패({str(e)[:80]}) → 고정값 {FALLBACK}")
            _cache.update(rate=FALLBACK, ts=now, source="fallback",
                          updated=None)
        else:
            print(f"  ⚠️ 환율 갱신 실패({str(e)[:80]}) → 직전 값 유지")
            _cache["ts"] = now
    return {k: v for k, v in _cache.items() if k != "rates"}


async def rate_to_usd(currency: str) -> float:
    """1 <currency> 가 몇 USD 인가.

    [왜 필요한가]
    KIS 해외 시세는 거래소 통화로 온다 — 도쿄는 엔, 홍콩은 홍콩달러다.
    그걸 그대로 USDC 로 쓰면 토요타 3,067엔이 $3,067 이 된다(약 150배).
    원장의 기준 통화가 USDC 인 이상, 들어오는 지점에서 반드시 환산한다.

    같은 응답(open.er-api.com)에 전 통화가 들어 있으므로 추가 호출이 없다.
    """
    cur = (currency or "USD").upper()
    if cur == "USD":
        return 1.0

    info = await usd_krw()                 # 캐시를 채우고 원본 rates 를 남긴다
    rates = _cache.get("rates") or {}
    per_usd = rates.get(cur)
    if not per_usd:
        raise ValueError(f"환율 없음: {cur} (출처 {info['source']})")
    return 1.0 / float(per_usd)


def to_krw(micro_usdc: int | float, rate: float) -> int:
    """마이크로 USDC → 원. 화면에 찍히는 값이라 정수로 반올림한다."""
    return int(round(micro_usdc / 1_000_000 * rate))
