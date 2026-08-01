"""이중 만다트 (Managed v2).

CognitiveMandate  revenue → research   "생각할 돈" 청구
CapitalMandate    treasury → invest    "투자할 돈" 청구

트랙 요건 '결제 요청 생성·입금'이 두 축으로 충족된다.
둘 다 자동 승인만큼 자동 거절이 가능해야 진짜 만다트다 —
CapitalMandate의 성과 하한이 그 거절 조건이다.
"""
from __future__ import annotations

import time

from app import config
from app.core.ledger import LEDGER
from app.models import Invoice, InvoiceStatus

WEEK_SECONDS = 7 * 86_400


def _week_index(ts: float | None = None) -> int:
    return int((ts or time.time()) // WEEK_SECONDS)


def issue_invoice(kind: str, issuer: str, payer: str, balance: int, needed: int,
                  expected_value: int, reason: str) -> Invoice:
    """에이전트가 스스로 부족액을 계산해 청구서를 발행한다."""
    shortfall = max(0, needed - balance)
    return Invoice(kind=kind, issuer=issuer, payer=payer, amount=shortfall,
                   reason=reason, expected_value=expected_value)


class _WeeklyBudget:
    """주간 한도의 롤오버를 담당한다.

    [왜 필요한가]
    weekly_paid는 증가만 하고 리셋되는 곳이 없었다. 이름은 '주간'인데
    실제로는 프로세스 수명 전체의 영구 한도였다. COG_MANDATE_WEEKLY_CAP
    ($10)을 소진하면 research-agent 조달이 영구 중단되고, 봇은 뉴스조차
    못 산다 — "스스로 벌어서 쓴다"는 이 프로젝트의 핵심 주장이
    장시간 자동 운전에서 조용히 무너진다.

    SpendTracker._rollover(일일)와 BotInstance.roll_day(일일)에는
    이미 같은 장치가 있었다. 만다트에만 빠져 있었다.
    """

    def __init__(self) -> None:
        self.weekly_paid = 0
        self._week = _week_index()
        self.history: list[Invoice] = []

    def _rollover(self) -> None:
        wk = _week_index()
        if wk != self._week:
            self._week = wk
            self.weekly_paid = 0

    def room(self, cap: int) -> int:
        self._rollover()
        return cap - self.weekly_paid

    def charge(self, amount: int) -> None:
        self._rollover()
        self.weekly_paid += amount


class CognitiveMandate(_WeeklyBudget):
    """판매수익 지갑의 자동승인 정책. 부분 승인 지원
    (은행 자동이체가 잔고만큼만 나가는 것과 같은 시맨틱)."""

    async def process(self, inv: Invoice) -> Invoice:
        if inv.amount == 0:
            inv.status, inv.decided_reason = InvoiceStatus.REJECTED, "부족액 없음"
        elif not inv.issuer.startswith("research-agent@"):
            inv.status, inv.decided_reason = InvoiceStatus.REJECTED, f"청구 권한 없음: {inv.issuer}"
        elif inv.expected_roi < config.COG_MANDATE_MIN_ROI:
            inv.status = InvoiceStatus.REJECTED
            inv.decided_reason = f"기대 ROI 미달: {inv.expected_roi:.1f}x"
        else:
            weekly_room = self.room(config.COG_MANDATE_WEEKLY_CAP)
            available = (await LEDGER.snapshot()).get(inv.payer, 0)
            payable = min(inv.amount, weekly_room, available)
            if payable <= 0:
                inv.status = InvoiceStatus.REJECTED
                inv.decided_reason = f"지급 불가 (주간잔여 {weekly_room}, 재원 {available})"
            else:
                await LEDGER.transfer(inv.payer, inv.issuer, payable,
                                      f"invoice:{inv.invoice_id}")
                self.charge(payable)
                note = "전액" if payable == inv.amount else f"부분({payable}/{inv.amount})"
                inv.status = InvoiceStatus.APPROVED
                inv.decided_reason = f"인지 만다트 충족 — {note} 자동 승인"
                inv.amount = payable
        self.history.append(inv)
        return inv


class CapitalMandate(_WeeklyBudget):
    """사용자 트레저리의 자동승인 정책 — 인지 만다트보다 훨씬 빡빡하다.
    사용자 원금은 '가져도 되는 돈'이 아니라 '맡아두는 돈'이기 때문.

    거절 조건이 실질적이다:
      성과 하한   최근 적중률 40% 미만이면 추가 투입 거절
      노출 상한   총 투자 노출(열린 포지션 원가 합) 한도 초과 시 거절
    """

    async def process(self, inv: Invoice, hit_rate: float, cold_start: bool,
                      current_exposure: int) -> Invoice:
        if inv.amount == 0:
            inv.status, inv.decided_reason = InvoiceStatus.REJECTED, "부족액 없음"
        elif not inv.issuer.startswith("invest-wallet@"):
            inv.status, inv.decided_reason = InvoiceStatus.REJECTED, f"청구 권한 없음: {inv.issuer}"
        elif (not cold_start) and hit_rate < config.CAP_MANDATE_MIN_HIT_RATE:
            inv.status = InvoiceStatus.REJECTED
            inv.decided_reason = (f"성과 하한 미달: 적중률 {hit_rate:.0%} < "
                                  f"{config.CAP_MANDATE_MIN_HIT_RATE:.0%} — 추가 투입 거절")
        elif current_exposure + inv.amount > config.CAP_MANDATE_MAX_EXPOSURE:
            inv.status = InvoiceStatus.REJECTED
            inv.decided_reason = "총 노출 한도 초과"
        elif inv.amount > self.room(config.CAP_MANDATE_WEEKLY_CAP):
            inv.status, inv.decided_reason = InvoiceStatus.REJECTED, "주간 투입 한도 초과"
        else:
            available = (await LEDGER.snapshot()).get(inv.payer, 0)
            payable = min(inv.amount, available)
            if payable <= 0:
                inv.status, inv.decided_reason = InvoiceStatus.REJECTED, "트레저리 잔고 없음"
            else:
                await LEDGER.transfer(inv.payer, inv.issuer, payable,
                                      f"invoice:{inv.invoice_id}")
                self.charge(payable)
                inv.status = InvoiceStatus.APPROVED
                inv.decided_reason = f"자본 만다트 충족 (적중률 {hit_rate:.0%}) — 자동 승인"
                inv.amount = payable
        self.history.append(inv)
        return inv
