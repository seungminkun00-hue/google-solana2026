"""pay.sh / MPP 실전 클라이언트.

⚠️ 이 파일의 내용은 recon.py 실측(2026-07)에 기반한다.
   추측이 아니라 실제 402 응답을 덤프해서 만든 것.

실측으로 밝혀진 것:
  - 메서드는 GET (POST는 403)
  - 가격이 본문이 아니라 www-authenticate 헤더에 있음
  - 그 안에 base64로 한 번 더 감싸져 있음
  - 프로토콜 이름이 x402가 아니라 MPP (구조는 유사)

우리 코드(paid_fetch)는 price/pay_to라는 이름을 기대한다.
이 파일의 parse_mpp_challenge가 '통역사' 역할을 해서
실제 필드명(amount/recipient)을 우리 이름으로 바꿔 끼운다.
따라서 paid_fetch는 한 글자도 수정할 필요가 없다.
"""
from __future__ import annotations

import base64
import json
import re

import httpx

# 실측 확인된 샌드박스 라우트
SANDBOX_ROUTES = {
    "market_quote": "https://debugger.pay.sh/mpp/quote/{ticker}",
}


def parse_mpp_challenge(res: httpx.Response) -> dict:
    """402 응답 → 우리 코드가 쓰는 형식으로 통역.

    반환: {"price": int, "pay_to": str, "resource": str, ...}
    """
    wa = res.headers.get("www-authenticate", "")
    m = re.search(r'request="([^"]+)"', wa)
    if not m:
        # 폴백: 일부 구현은 본문에 실어 보낸다
        body = res.json()
        d = body.get("detail", body)
        if isinstance(d, dict) and "price" in d:
            return d
        raise ValueError(f"결제 요구사항을 찾을 수 없음: {wa[:120]}")

    raw = m.group(1)
    padded = raw + "=" * (-len(raw) % 4)          # base64 길이 보정
    data = json.loads(base64.urlsafe_b64decode(padded))

    return {
        "price":       int(data["amount"]),        # amount    → price
        "pay_to":      data["recipient"],          # recipient → pay_to
        "currency":    data.get("currency"),       # USDC 민트 주소
        "resource":    str(res.url.path),
        "network":     data.get("methodDetails", {}).get("network"),
        "decimals":    data.get("methodDetails", {}).get("decimals", 6),
        "challenge_id": re.search(r'id="([^"]+)"', wa).group(1) if 'id="' in wa else None,
    }


async def peek_price(client: httpx.AsyncClient, url: str) -> int:
    """가격만 확인하고 결제는 안 한다 (402 받는 건 공짜).

    should_think가 config의 추측 가격 대신 실제 가격을 쓰게 하는 용도.
    """
    res = await client.get(url, timeout=20.0)
    if res.status_code != 402:
        return 0
    return parse_mpp_challenge(res)["price"]
