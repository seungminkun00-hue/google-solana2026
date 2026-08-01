"""실시간 주가 어댑터 — pay.sh MPP 라우트 (실측 검증됨).

    pay --sandbox fetch https://debugger.pay.sh/mpp/quote/NVDA
    → {"symbol":"NVDA","price":"35.69","currency":"USD","source":"mpp-demo"}

[왜 CLI를 거치나]
이 라우트는 localnet MPP 결제를 요구한다. 402 챌린지를 받고 지갑으로
서명해 재요청하는 전 과정을 `pay` CLI가 대신 해준다. 우리가 직접
구현할 수도 있지만, 키 관리와 서명을 검증된 도구에 맡기는 편이 안전하다.

[중요] localnet 결제라 mainnet 실제 USDC가 필요 없다.
       Gemini 게이트웨이(mainnet 전용)와 결정적으로 다른 점.

[티커 매핑]
우리 미러 토큰은 NVDAx 처럼 x가 붙는다. 조회할 때는 떼고 보낸다.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time

BASE_URL = "https://debugger.pay.sh/mpp/quote"
TIMEOUT = 40
IS_WINDOWS = os.name == "nt"
CACHE_TTL = 20          # 초. 같은 종목을 연속 조회할 때 중복 결제를 막는다


class QuoteUnavailable(Exception):
    pass


_cache: dict[str, tuple[float, int]] = {}      # ticker → (조회시각, 가격)


def _symbol(ticker: str) -> str:
    """NVDAx → NVDA. 미러 토큰 접미사를 제거한다."""
    return ticker[:-1] if ticker.endswith("x") else ticker


def _run_pay(symbol: str) -> dict:
    """pay CLI로 402 결제 후 시세를 받아온다."""
    cmd = ["pay", "--sandbox", "fetch", f"{BASE_URL}/{symbol}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=TIMEOUT, encoding="utf-8", errors="replace",
                           shell=IS_WINDOWS)
    except FileNotFoundError:
        raise QuoteUnavailable("pay CLI 없음 — npm install -g @solana/pay")
    except subprocess.TimeoutExpired:
        raise QuoteUnavailable(f"{symbol} 시세 조회 시간 초과")

    # CLI가 진행 로그를 함께 출력하므로 JSON 줄만 골라낸다
    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and "price" in line:
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    raise QuoteUnavailable(
        f"{symbol} 응답 파싱 실패: {(r.stderr or r.stdout or '')[:150]}")


def spot_live(ticker: str) -> int:
    """마이크로 USDC 단위 현재가. 실패하면 QuoteUnavailable."""
    now = time.time()
    hit = _cache.get(ticker)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    data = _run_pay(_symbol(ticker))
    price = int(float(data["price"]) * 1_000_000)
    if price <= 0:
        raise QuoteUnavailable(f"{ticker} 가격이 0 이하: {data}")
    _cache[ticker] = (now, price)
    return price


async def spot_live_async(ticker: str) -> int:
    """CLI 호출은 블로킹이므로 별도 스레드에서 돌린다.

    안 그러면 서버 전체가 몇 초씩 멈춘다.
    """
    return await asyncio.to_thread(spot_live, ticker)


def available() -> bool:
    """PRICE_SOURCE=live 이면 실전 시세를 쓴다.

    pay CLI 존재 확인은 하지 않는다. `pay --version`은 이 환경에서
    바이너리 재설치를 시도하다 unzip 부재로 실패하므로 신뢰할 수 없다.
    실제 조회가 실패하면 spot()이 내장 기준가로 폴백하므로,
    여기서 미리 막을 이유가 없다.
    """
    return os.environ.get("PRICE_SOURCE", "mock").lower() == "live"
