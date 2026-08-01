"""x402 공급자 측 (보안 강화판).

[감사 취약점 1 수정]
이전 구현은 LEDGER.proofs를 선형 탐색해 payee·amount만 확인했다.
결과: ① 동일 증빙 무한 재사용 ② 다른 엔드포인트에 교차 사용
     ③ O(n) 탐색으로 시간이 갈수록 느려짐

수정본은 ProofRegistry에 위임한다:
  - resource 바인딩: /sell/signal/A 결제로 /sell/signal/B 접근 불가
  - consumed 플래그: 1회 결제 1회 사용
  - TTL: 오래된 증빙 거부
  - O(1) 해시 조회
"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.core.proofs import PROOFS, ProofRejected


def paywall_dynamic(price: int, pay_to_fn):
    """유료 엔드포인트 의존성. 수취 지갑을 요청에서 동적으로 결정한다.
    예) /bots/bot2/sell/… → revenue-wallet@bot2

    resource는 실제 요청 경로에서 얻는다 — 경로 파라미터(signal_id 등)까지
    포함되어야 증빙 교차 사용이 막힌다.
    """

    async def _guard(request: Request,
                     x_payment: str | None = Header(default=None)) -> str:
        pay_to = pay_to_fn(request)
        resource = request.url.path
        if x_payment is None:
            raise HTTPException(
                status_code=402,
                detail={"price": price, "pay_to": pay_to, "asset": "USDC",
                        "network": "solana", "resource": resource})
        try:
            PROOFS.consume(x_payment, pay_to, price, resource)
        except ProofRejected as e:
            raise HTTPException(status_code=402,
                                detail={"error": str(e), "resource": resource})
        return x_payment

    return _guard


def paywall(price: int, pay_to: str):
    """수취 지갑이 고정된 페이월 (모의 외부 세계 라우트용).

    [2026-08] paywall_dynamic과 20줄이 글자 그대로 같았다. 한쪽만 고치면
    다른 쪽에 구멍이 남는 구조라, 고정 수취인을 상수 함수로 감싸 통합했다.
    두 함수 다 살아 있다 — 모의 라우트는 external 고정, 봇 판매 창구는
    경로에서 결정하므로 호출부 문법은 그대로 둔다.
    """
    return paywall_dynamic(price, lambda _request: pay_to)
