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
    """이 종목을 룰북에 넣어도 되는가.

    사용자가 존재하지 않는 종목을 넣으면 봇이 영원히 아무것도 못 사는데,
    그 사실을 미리 알려줄 수 있다.

    [2026-08-03] 기준이 둘이 됐다.
      ① smartmoney.market SEC 추적 목록 (미국 종목)
      ② 우리 시장 카탈로그 (app/core/markets.py)
    ②가 필요한 이유는 코스피·코스닥·도쿄 종목이 SEC 목록에 없기 때문이다.
    그 종목들은 KIS 로 시세가 실제로 오고 devnet 에 민트도 있는, 즉
    **살 수 있는 것이 증명된** 종목이다. SEC 목록에 없다고 막으면
    국내장이 통째로 막힌다.
    """
    sym = ticker[:-1] if ticker.endswith("x") else ticker
    try:
        from app.core.markets import QUOTE_SPEC
        if sym in QUOTE_SPEC or sym.upper() in QUOTE_SPEC:
            return True
    except Exception:                                     # noqa: BLE001
        pass
    try:
        return sym in load()["stocks"]
    except Exception:
        return True          # 조회 실패 시 막지 않는다 (폴백)


def company_name(ticker: str) -> str:
    """사람이 읽는 종목명.

    국내·일본 종목은 SEC 목록에 없다. 시장 카탈로그가 KIS 에서 받아둔
    이름(삼성전자·에코프로비엠 …)을 먼저 본다 — 화면에 종목코드만
    뜨면 무엇을 샀는지 알 수 없다.
    """
    sym = ticker[:-1] if ticker.endswith("x") else ticker
    try:
        from app.core.markets import NAMES
        hit = NAMES.get(sym) or NAMES.get(sym.upper())
        if hit and hit != sym:
            return hit
    except Exception:                                     # noqa: BLE001
        pass
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
