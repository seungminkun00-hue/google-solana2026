"""x402 소비자 측 (보안·정합성 강화판).

수정 내역:
  [정합성] 결제 시 resource를 402 챌린지가 지정한 값으로 사용.
           이전에는 full URL을 썼는데 서버는 path만 보므로 불일치했다.
  [보상]   결제 후 2차 요청이 실패하면 지출 기록을 되돌린다.
           이전에는 돈만 나가고 물건은 못 받는 구멍이 있었다.
  [롤오버] spent_today가 영원히 누적되던 문제 수정. 자정 기준 리셋.
  [세션]   만료 시 자동 갱신 훅. 데모가 1시간 후 조용히 죽던 문제 수정.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

import httpx

from app.core.ledger import LEDGER
from app.models import PaymentProof, SpendPolicy


class BudgetExceeded(Exception):
    pass


class SessionExpired(Exception):
    pass


class KillSwitchActive(Exception):
    pass


class PaymentFailed(Exception):
    pass


def _day_index(ts: float | None = None) -> int:
    return int((ts or time.time()) // 86_400)


class SpendTracker:
    """온체인 policy_vault의 오프체인 미러."""

    def __init__(self, wallet: str, policy: SpendPolicy,
                 auto_renew_seconds: int = 0) -> None:
        self.wallet = wallet
        self.policy = policy
        self.auto_renew_seconds = auto_renew_seconds
        self.spent_today = 0
        self.decision_spent = 0
        self.receipts: list[PaymentProof] = []
        self._day = _day_index()

    # 예산 롤오버
    def _rollover(self) -> None:
        today = _day_index()
        if today != self._day:
            self._day = today
            self.spent_today = 0

    def begin_decision(self) -> None:
        self._rollover()
        self.decision_spent = 0

    # 정책 검사
    def check(self, amount: int) -> None:
        self._rollover()
        if self.policy.killed:
            raise KillSwitchActive("킬 스위치 활성 — 전 지출 정지")
        if time.time() > self.policy.session_expires_at:
            if self.auto_renew_seconds:      # 위임 자동 갱신 훅
                self.policy.session_expires_at = int(time.time()) + self.auto_renew_seconds
            else:
                raise SessionExpired("세션키 만료 — 재위임 필요")
        if self.decision_spent + amount > self.policy.per_decision_cap:
            raise BudgetExceeded(
                f"결정당 상한 초과: {self.decision_spent + amount} > {self.policy.per_decision_cap}")
        if self.spent_today + amount > self.policy.daily_cap:
            raise BudgetExceeded(
                f"일일 상한 초과: {self.spent_today + amount} > {self.policy.daily_cap}")

    def record(self, proof: PaymentProof) -> None:
        self.spent_today += proof.amount
        self.decision_spent += proof.amount
        self.receipts.append(proof)

    def rollback(self, proof: PaymentProof) -> None:
        """2차 요청 실패 시 지출 카운터 원복.
        실제 USDC는 회수 불가 — 실전에서는 공급자 SLA/에스크로가 필요한
        잔여 리스크로 남는다."""
        self.spent_today -= proof.amount
        self.decision_spent -= proof.amount
        if proof in self.receipts:
            self.receipts.remove(proof)


async def paid_fetch(
    client: httpx.AsyncClient,
    url: str,
    tracker: SpendTracker,
    payload: dict | None = None,
) -> tuple[dict, PaymentProof | None]:
    """402 챌린지 -> 정책검사 -> 결제 -> 증빙 첨부 재요청."""
    res = await client.post(url, json=payload or {})
    if res.status_code != 402:
        res.raise_for_status()
        return res.json(), None

    body = res.json()
    challenge = body.get("detail", body)
    amount = int(challenge["price"])
    pay_to = challenge["pay_to"]
    # 서버가 지정한 resource를 그대로 쓴다. 없으면 URL의 path로 폴백.
    resource = challenge.get("resource") or urlparse(url).path

    tracker.check(amount)
    proof = await LEDGER.transfer(tracker.wallet, pay_to, amount, resource)
    tracker.record(proof)

    res = await client.post(url, json=payload or {},
                            headers={"X-PAYMENT": proof.proof_id})
    if res.status_code != 200:
        tracker.rollback(proof)
        raise PaymentFailed(f"결제 후 요청 실패 ({res.status_code}): {res.text[:120]}")
    return res.json(), proof
