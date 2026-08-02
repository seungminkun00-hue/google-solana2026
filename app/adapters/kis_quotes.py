"""실시간 시세 — 한국투자증권(KIS) OpenAPI.

    국내주식  GET /uapi/domestic-stock/v1/quotations/inquire-price   tr_id FHKST01010100
    해외주식  GET /uapi/overseas-price/v1/quotations/price           tr_id HHDFS00000300

[왜 KIS 인가]
코스피·코스닥을 다루려면 국내 시세가 필요한데, 해외 무료 API 는 한국 종목
커버리지가 없다(Alpha Vantage 로 `005930.KS` 를 물으면 빈 응답이 온다 —
2026-08-02 실측). KIS 는 국내와 해외를 한 키로 준다.

⚠️ **appsecret 이 있어야 동작한다.**
    2026-08-02 기준 이 리포지토리에는 appkey 만 있다. appkey 만으로 토큰
    엔드포인트를 두드리면 `EGW00105 유효하지 않은 AppSecret입니다` 가
    돌아온다 — 즉 appkey 자체는 인정되고 secret 만 비어 있는 상태다.
    `.env` 에 KIS_APP_SECRET 을 넣는 순간 켜진다.

[토큰]
발급은 하루 1회 수준으로 제한된다(같은 키로 자주 부르면 거부).
그래서 파일에 캐시하고 만료 전까지 재사용한다. 프로세스를 재시작해도
남아 있어야 하므로 메모리가 아니라 파일이다.

[한도]
초당 호출 제한이 있다(실전 20건/초, 모의 2건/초 수준). 시세는 종목당
캐시 TTL 을 두고, 사이클이 같은 종목을 여러 번 물어도 한 번만 나간다.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time

import httpx

REAL = "https://openapi.koreainvestment.com:9443"
PAPER = "https://openapivts.koreainvestment.com:29443"

TOKEN_PATH = pathlib.Path("wallets") / "kis_token.json"
PRICE_TTL = 30          # 종목당 시세 캐시 (초)
TIMEOUT = 15

# ticker -> (받은 시각, 원 통화 가격, 통화)
_CACHE: dict[str, tuple[float, float, str]] = {}

# 초당 호출 제한(EGW00201 "초당 거래건수를 초과하였습니다") 회피.
# 한 사이클이 여러 종목의 시세를 연달아 물으면 실측으로 바로 걸린다.
# 호출을 직렬화하고 최소 간격을 둔다 — 앞뒤로 몇십 밀리초일 뿐이라
# 사이클 체감 속도에는 영향이 없다.
MIN_INTERVAL = 0.25
_gate = asyncio.Lock()
_last_call = 0.0


async def _spaced(fn, *args):
    """최소 간격을 지켜 호출한다. 한도에 걸리면 한 번 더 쉬었다 재시도.

    ⚠️ **여기 넘기는 fn 안에서 다시 _spaced 를 부르면 안 된다.**
    asyncio.Lock 은 재진입이 안 되므로 그 자리에서 영원히 멈춘다
    (실측: 검증 스크립트가 17분간 아무것도 못 하고 매달려 있었다).
    호출 두 개가 필요하면 각각 따로 _spaced 로 감싼다.
    """
    global _last_call
    async with _gate:
        gap = time.monotonic() - _last_call
        if gap < MIN_INTERVAL:
            await asyncio.sleep(MIN_INTERVAL - gap)
        try:
            return await fn(*args)
        except QuoteUnavailable as e:
            if "EGW00201" not in str(e):
                raise
            await asyncio.sleep(1.0)
            return await fn(*args)
        finally:
            _last_call = time.monotonic()


class QuoteUnavailable(Exception):
    pass


def _base() -> str:
    return PAPER if os.environ.get("KIS_ENV", "real").lower() == "paper" else REAL


def enabled() -> bool:
    """키 두 개가 다 있을 때만 켠다.

    appkey 만 있으면 토큰이 안 나오므로 켜봐야 매 호출이 실패한다.
    '켰다고 생각했는데 안 켜진' 상태를 만들지 않으려고 여기서 함께 본다.
    """
    return bool(os.environ.get("KIS_APP_KEY") and os.environ.get("KIS_APP_SECRET"))


async def _token() -> str:
    """접근 토큰. 파일 캐시가 살아 있으면 그걸 쓴다."""
    try:
        cached = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
        if cached.get("expires_at", 0) > time.time() + 300:
            return cached["access_token"]
    except Exception:                                     # noqa: BLE001
        pass

    body = {"grant_type": "client_credentials",
            "appkey": os.environ["KIS_APP_KEY"],
            "appsecret": os.environ["KIS_APP_SECRET"]}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{_base()}/oauth2/tokenP", json=body)
    if r.status_code != 200:
        raise QuoteUnavailable(f"토큰 발급 실패 {r.status_code}: {r.text[:160]}")

    data = r.json()
    token = data.get("access_token")
    if not token:
        raise QuoteUnavailable(f"토큰 없음: {str(data)[:160]}")

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps({
        "access_token": token,
        # expires_in 은 초 단위(보통 86400). 조금 일찍 만료로 본다.
        "expires_at": time.time() + int(data.get("expires_in", 86_400)) - 600,
    }), encoding="utf-8")
    return token


def _headers(token: str, tr_id: str) -> dict:
    return {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": os.environ["KIS_APP_KEY"],
        "appsecret": os.environ["KIS_APP_SECRET"],
        "tr_id": tr_id,
    }


async def _domestic(token: str, code: str) -> float:
    """국내 종목 현재가 (원). code 는 6자리 숫자 — 예: 삼성전자 005930."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            f"{_base()}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=_headers(token, "FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})
    data = r.json()
    price = (data.get("output") or {}).get("stck_prpr")
    if not price:
        raise QuoteUnavailable(f"국내 시세 없음({code}): {str(data)[:160]}")
    return float(price)


# 거래소 → 그 거래소가 쓰는 통화.
#
# ⚠️ 이 표가 없으면 안 된다. 해외 시세 응답에는 통화 필드가 **없고**
#    (2026-08-02 실측: rsym·base·last·pvol·tvol 뿐), 값은 그 거래소의
#    현지 통화로 온다. USD 로 가정하면 토요타 3,067엔이 $3,067 이 되어
#    약 150배로 평가된다. 통화를 모르는 거래소는 추측하지 않고 거부한다.
EXCHANGE_CURRENCY = {
    "NAS": "USD", "NYS": "USD", "AMS": "USD",   # 미국
    "TSE": "JPY",                               # 도쿄
    "HKS": "HKD",                               # 홍콩
    "SHS": "CNY", "SZS": "CNY",                 # 상하이·선전
    "HSX": "VND", "HNX": "VND",                 # 베트남
}


async def _overseas(token: str, symbol: str, excd: str) -> float:
    """해외 종목 현재가 (거래소 통화). excd 는 거래소 코드."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(
            f"{_base()}/uapi/overseas-price/v1/quotations/price",
            headers=_headers(token, "HHDFS00000300"),
            params={"AUTH": "", "EXCD": excd, "SYMB": symbol})
    data = r.json()
    price = (data.get("output") or {}).get("last")
    if not price:
        raise QuoteUnavailable(f"해외 시세 없음({symbol}): {str(data)[:160]}")
    return float(price)


async def price(ticker: str, *, code: str = "", excd: str = "") -> tuple[float, str]:
    """(가격, 통화). code 가 있으면 국내, 없으면 해외로 조회한다.

    실패하면 QuoteUnavailable — 부르는 쪽이 내장 기준가로 내려간다.
    시세를 못 받았다고 매매를 멈추면 데모가 통째로 죽는다.
    """
    if not enabled():
        raise QuoteUnavailable("KIS 키가 없습니다 (KIS_APP_KEY/KIS_APP_SECRET)")

    hit = _CACHE.get(ticker)
    if hit and time.time() - hit[0] < PRICE_TTL:
        return hit[1], hit[2]

    token = await _token()
    if code:
        value, currency = await _spaced(_domestic, token, code), "KRW"
    else:
        # ⚠️ 미러 토큰 표기(MSFTx)를 그대로 보내면 KIS 는 빈 응답을 준다.
        #    거래소에 그런 심볼이 없기 때문이다. 접미사를 떼고 보낸다.
        symbol = ticker[:-1] if ticker.endswith("x") else ticker
        market = (excd or "NAS").upper()
        currency = EXCHANGE_CURRENCY.get(market)
        if currency is None:
            raise QuoteUnavailable(
                f"통화를 모르는 거래소({market}) — 환산할 수 없어 거부합니다")
        value = await _spaced(_overseas, token, symbol, market)

    _CACHE[ticker] = (time.time(), value, currency)
    return value, currency
