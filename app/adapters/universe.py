"""종목 유니버스 어댑터 — smartmoney.market 무료 라우트.

    GET https://pay.smartmoney.market/api/insider-tracked-stocks   (무료)
    → 1,045개 실제 종목 (SEC Form 4 내부자 추적 대상)

    GET https://pay.smartmoney.market/api/fund-universe            (무료)
    → 510개 실제 헤지펀드 (13-F 공시 기반)

[왜 중요한가]
룰북의 종목 목록이 우리가 지어낸 4개에서 **실제 SEC 추적 대상**으로
바뀐다. 사용자가 "내부자 매수가 있는 종목만" 같은 규칙을 세울 때,
그 종목 목록이 진짜여야 규칙도 진짜다.

[비용] 무료. mainnet 결제가 필요 없다.
       유료 라우트(/api/ticker/{symbol} 등)는 mainnet $0.01/회.

[캐시] 종목 목록은 자주 안 바뀌므로 파일로 저장해두고 재사용한다.
       매번 받아오면 발표 당일 네트워크가 끊겼을 때 데모가 죽는다.
"""
from __future__ import annotations

import json
import pathlib
import time

import httpx

BASE = "https://pay.smartmoney.market"
CACHE_PATH = pathlib.Path("wallets") / "universe.json"
CACHE_TTL = 86_400          # 하루


def _fetch(path: str) -> dict:
    with httpx.Client(timeout=30) as c:
        r = c.get(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()


def load(force: bool = False) -> dict:
    """종목·펀드 목록을 가져온다. 캐시가 살아있으면 그걸 쓴다."""
    if not force and CACHE_PATH.exists():
        try:
            cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if time.time() - cached.get("fetched_at", 0) < CACHE_TTL:
                return cached
        except Exception:
            pass

    stocks = _fetch("/api/insider-tracked-stocks")
    funds = _fetch("/api/fund-universe")
    data = {
        "fetched_at": time.time(),
        "source": BASE,
        "stock_count": stocks.get("tracked_stock_count", 0),
        "fund_count": funds.get("fund_count", 0),
        "stocks": {s["ticker"]: s["company"] for s in stocks.get("stocks", [])},
        "funds": [f["short_name"] for f in funds.get("funds", [])],
    }
    CACHE_PATH.parent.mkdir(exist_ok=True)
    CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def is_tracked(ticker: str) -> bool:
    """이 종목이 실제 SEC 내부자 추적 대상인가.

    룰북 검증에 쓴다. 사용자가 존재하지 않는 종목을 넣으면
    봇이 영원히 아무것도 못 사는데, 그 사실을 미리 알려줄 수 있다.
    """
    sym = ticker[:-1] if ticker.endswith("x") else ticker
    try:
        return sym in load()["stocks"]
    except Exception:
        return True          # 조회 실패 시 막지 않는다 (폴백)


def company_name(ticker: str) -> str:
    sym = ticker[:-1] if ticker.endswith("x") else ticker
    try:
        return load()["stocks"].get(sym, sym)
    except Exception:
        return sym


if __name__ == "__main__":
    d = load(force=True)
    print(f"✅ smartmoney.market 유니버스 (무료 라우트)")
    print(f"   추적 종목 {d['stock_count']:,}개  |  운용사 {d['fund_count']}개")
    print(f"   캐시 → {CACHE_PATH}\n")
    print("   우리 미러 토큰 검증")
    for t in ["NVDAx", "TSLAx", "SPYx", "AAPLx", "MSFTx"]:
        ok = is_tracked(t)
        print(f"     {t:7s} {'✅ ' + company_name(t) if ok else '❌ 추적 대상 아님'}")
