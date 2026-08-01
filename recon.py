"""정찰 스크립트 — pay.sh가 실제로 어떻게 생겼는지 눈으로 확인한다.

⚠️ 이 스크립트는 돈을 쓰지 않는다.
   402(가격표)를 받는 것까지만 하고 결제는 안 한다.
   가게에 들어가 가격표만 보고 나오는 것과 같다.

사용법:
    python recon.py                       # 기본 후보 전부 확인
    python recon.py <URL>                 # 특정 주소만 확인
"""
import asyncio
import base64
import json
import re
import sys

import httpx

# 실측으로 확인된 주소 (2026-07 기준)
CANDIDATES = [
    ("GET", "https://debugger.pay.sh/mpp/quote/AAPL"),   # ✅ 실제 402 확인됨
    ("GET", "https://debugger.pay.sh/mpp/quote/NVDA"),
]


def decode_www_authenticate(header: str) -> dict | None:
    """MPP 방식: www-authenticate 헤더 안에 base64로 결제 요구사항이 들어있다.

    실측 결과:
      www-authenticate: Payment id="...", method="solana", intent="charge",
                        request="eyJhbW91bnQiOiIxMDAwMC..."
      request를 base64 디코딩하면:
      {"amount":"10000", "currency":"<USDC민트>", "recipient":"<받는지갑>", ...}
    """
    m = re.search(r'request="([^"]+)"', header)
    if not m:
        return None
    raw = m.group(1)
    padded = raw + "=" * (-len(raw) % 4)          # base64 길이 보정
    try:
        return json.loads(base64.urlsafe_b64decode(padded))
    except Exception as e:
        print(f"     ⚠️ base64 디코딩 실패: {e}")
        return None


async def probe(method: str, url: str) -> None:
    print("=" * 68)
    print(f"🔍 {method} {url}")
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            res = await c.request(method, url)
    except Exception as e:
        print(f"  ❌ 연결 실패: {type(e).__name__}: {e}")
        return

    print(f"  상태코드: {res.status_code}")
    if res.status_code == 402:
        print("  ✅ 402 Payment Required — 결제 가능한 라우트 확인!")
    elif res.status_code == 403:
        print("  ⚠️ 403 — 이 경로는 API 키가 필요. 다른 경로/메서드를 시도해볼 것")
    elif res.status_code == 404:
        print("  ⚠️ 404 — 주소가 틀림")
    else:
        print("  ⚠️ 402가 아님 — 주소 또는 메서드(GET/POST) 확인 필요")

    # ── 결제 헤더 ───────────────────────────────────────────
    wa = res.headers.get("www-authenticate")
    if wa:
        print(f"\n  📋 www-authenticate 헤더 발견")
        keys = [k for k, _ in re.findall(r'(\w+)="([^"]*)"', wa)]
        print(f"     포함된 키: {keys}")
        decoded = decode_www_authenticate(wa)
        if decoded:
            print("\n  🔓 base64 디코딩 결과 (여기 진짜 가격이 있음)")
            print("     " + json.dumps(decoded, indent=2,
                                       ensure_ascii=False).replace("\n", "\n     "))
            print("\n  🔧 우리 코드 필드로 매핑하면")
            print(f"     price    ← amount    = {decoded.get('amount')}")
            print(f"     pay_to   ← recipient = {decoded.get('recipient')}")
            net = decoded.get("methodDetails", {}).get("network")
            print(f"     network              = {net}")
    else:
        print("\n  📋 www-authenticate 헤더 없음")

    # ── 본문 ────────────────────────────────────────────────
    try:
        print("\n  📦 응답 본문")
        print("     " + json.dumps(res.json(), indent=2,
                                   ensure_ascii=False)[:600].replace("\n", "\n     "))
    except Exception:
        print(f"\n  📦 본문(텍스트): {res.text[:200]}")


async def main():
    targets = [("GET", u) for u in sys.argv[1:]] or CANDIDATES
    for method, url in targets:
        await probe(method, url)
    print("=" * 68)
    print("\n💡 다음 단계")
    print("   1. 위 '디코딩 결과'가 나왔다면 → _parse_challenge를 그 구조로 수정")
    print("   2. npm install -g @solana/pay  →  pay setup")
    print("   3. pay --sandbox curl <위 URL>   ← 실제 결제까지 되는지 확인")

asyncio.run(main())
