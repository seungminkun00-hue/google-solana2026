"""시장별 후보 종목을 KIS 로 실검증한다.

[왜 필요한가]
지수 구성종목을 기억으로 적으면 반드시 틀린다 — 상장폐지·합병·코드 변경이
있고, 코스닥인 줄 알았던 종목이 코스피이기도 하다. 틀린 종목으로 미러
토큰을 발행하면 그 토큰은 영원히 시세를 못 받는다(= 살 수 없는 종목).

그래서 발행 **전에** 한 종목씩 실제로 조회해 보고, 값이 오는 것만 남긴다.
종목명도 응답에서 받아온다. 우리가 지어내지 않는다.

    py -3.13 check_markets.py            # 검증만 하고 결과를 파일로
    py -3.13 check_markets.py --apply    # markets.py 에 넣을 코드도 출력

결과: wallets/market_candidates.json
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

from app import config          # noqa: F401  (.env 로드)
from app.adapters import fx as FX
from app.adapters import kis_quotes as kis

OUT = pathlib.Path("wallets") / "market_candidates.json"

# 후보. 여기서 살아남는 것만 시장에 들어간다.
#   국내는 6자리 종목코드, 해외는 (심볼, 거래소코드).
CANDIDATES: dict[str, dict] = {
    "kospi": {
        "name": "대한민국 · 코스피",
        "codes": ["005930", "000660", "373220", "207940", "005380",
                  "000270", "068270", "105560", "005490", "055550",
                  "035420", "012330", "028260", "051910", "006400",
                  "086790", "034730", "015760", "032830", "035720"],
    },
    "kosdaq": {
        "name": "대한민국 · 코스닥",
        "codes": ["247540", "086520", "196170", "028300", "403870",
                  "240810", "058470", "357780", "005290", "293490",
                  "067310", "141080", "214150", "095340", "039030",
                  "078600", "222800", "108320", "277810", "263750"],
    },
    "us-nasdaq": {
        "name": "미국 · 나스닥",
        "symbols": [("AAPL", "NAS"), ("MSFT", "NAS"), ("NVDA", "NAS"),
                    ("GOOGL", "NAS"), ("AMZN", "NAS"), ("META", "NAS"),
                    ("AVGO", "NAS"), ("TSLA", "NAS"), ("COST", "NAS"),
                    ("NFLX", "NAS"), ("AMD", "NAS"), ("PEP", "NAS"),
                    ("ADBE", "NAS"), ("CSCO", "NAS"), ("TMUS", "NAS"),
                    ("INTC", "NAS"), ("QCOM", "NAS"), ("AMAT", "NAS"),
                    ("INTU", "NAS"), ("BKNG", "NAS")],
    },
    "jp-tse": {
        "name": "일본 · 도쿄증권거래소",
        "symbols": [("7203", "TSE"), ("6758", "TSE"), ("9984", "TSE"),
                    ("6861", "TSE"), ("8306", "TSE"), ("9432", "TSE"),
                    ("6098", "TSE"), ("4063", "TSE"), ("8035", "TSE"),
                    ("6501", "TSE"), ("7974", "TSE"), ("9433", "TSE"),
                    ("4568", "TSE"), ("6902", "TSE"), ("8058", "TSE"),
                    ("7267", "TSE"), ("4502", "TSE"), ("6367", "TSE"),
                    ("8316", "TSE"), ("6981", "TSE")],
    },
}


async def _name_of(token: str, code: str) -> str:
    """국내 종목명. 시세 응답에는 이름이 없어서 따로 물어본다
    (inquire-price 는 업종·시장명만 준다 — 2026-08-02 실측)."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{kis._base()}/uapi/domestic-stock/v1/quotations/search-stock-info",
            headers=kis._headers(token, "CTPF1002R"),
            params={"PRDT_TYPE_CD": "300", "PDNO": code})
    o = (r.json() or {}).get("output") or {}
    return o.get("prdt_abrv_name") or o.get("prdt_name") or code


async def probe_domestic(token: str, code: str) -> dict | None:
    """국내 종목 하나. 이름까지 받아온다."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{kis._base()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=kis._headers(token, "FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    o = (r.json() or {}).get("output") or {}
    if not o.get("stck_prpr"):
        return None
    # ⚠️ 여기서 kis._spaced 를 다시 부르면 락이 겹쳐 멈춘다. 이 함수 자체가
    #    이미 _spaced 안에서 돌고 있으므로 그냥 부른다.
    name = await _name_of(token, code)
    return {"ticker": code, "name": name,
            "price": float(o["stck_prpr"]), "currency": "KRW",
            "market_kor": o.get("rprs_mrkt_kor_name", ""),
            "spec": {"code": code}}


async def probe_overseas(token: str, symbol: str, excd: str) -> dict | None:
    import httpx
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            f"{kis._base()}/uapi/overseas-price/v1/quotations/price",
            headers=kis._headers(token, "HHDFS00000300"),
            params={"AUTH": "", "EXCD": excd, "SYMB": symbol})
    o = (r.json() or {}).get("output") or {}
    if not o.get("last"):
        return None
    # 해외 응답에는 통화 필드가 없다. 거래소로 정한다 — kis_quotes 의
    # EXCHANGE_CURRENCY 와 같은 표를 쓴다(추측 금지).
    cur = kis.EXCHANGE_CURRENCY.get(excd.upper())
    return {"ticker": symbol, "name": symbol,
            "price": float(o["last"]), "currency": cur or "?",
            "market_kor": excd, "spec": {"excd": excd}}


async def main() -> None:
    if not kis.enabled():
        print("KIS 키가 없습니다 (.env 의 KIS_APP_KEY / KIS_APP_SECRET)")
        raise SystemExit(1)

    token = await kis._token()
    out: dict[str, dict] = {}

    for key, spec in CANDIDATES.items():
        rows, dead = [], []
        print(f"\n=== {spec['name']}")
        targets = ([("dom", c) for c in spec.get("codes", [])]
                   + [("ovs", s) for s in spec.get("symbols", [])])
        for kind, t in targets:
            try:
                # 초당 한도를 피해 직렬로, 간격을 두고 부른다.
                if kind == "dom":
                    row = await kis._spaced(probe_domestic, token, t)
                else:
                    row = await kis._spaced(probe_overseas, token, t[0], t[1])
            except Exception as e:                        # noqa: BLE001
                row = None
                print(f"  ⚠️ {t} 조회 오류: {str(e)[:80]}")
            label = t if isinstance(t, str) else t[0]
            if row:
                rows.append(row)
                usd = row["price"] * await FX.rate_to_usd(row["currency"])
                row["usd"] = round(usd, 2)
                print(f"  ✅ {label:8} {row['name'][:16]:18} "
                      f"{row['price']:>12,.2f} {row['currency']:4} → ${usd:>10,.2f}")
            else:
                dead.append(label)
                print(f"  ❌ {label:8} 시세 없음 — 제외")
        out[key] = {"name": spec["name"], "ok": rows, "dropped": dead}

    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(v["ok"]) for v in out.values())
    print(f"\n검증 통과 {total}종 · 결과 저장 → {OUT}")

    if "--apply" in sys.argv:
        print("\n" + "=" * 60)
        for key, v in out.items():
            print(f'    "{key}": [')
            for r in v["ok"]:
                print(f'        ("{r["ticker"]}", "{r["name"]}", {r["spec"]}),')
            print("    ],")


if __name__ == "__main__":
    asyncio.run(main())
