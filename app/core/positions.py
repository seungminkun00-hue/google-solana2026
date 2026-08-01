"""포지션 관리 — 사서 들고 있다가 조건이 맞을 때 판다.

[왜 필요한가]
이전 구조는 매수와 청산이 한 사이클에 붙어 있었다. 데모에서는
손익을 바로 보여줄 수 있어 편했지만, 실제 투자 봇이 아니다.
사자마자 파는 건 투자가 아니라 수수료 내는 행위다.

이제 포지션은 열린 채로 남고, 매 주기마다 청산 조건을 검사한다.
사람이 자는 동안 손실을 끊고 이익을 확정하는 것이 자동화의 핵심이다.

[청산 사유 3가지 — 룰북이 정한다]
    take_profit  +N% 도달 → 이익 확정
    stop_loss    -N% 도달 → 손실 차단 (제일 중요)
    max_hold     N시간 초과 → 판단이 틀렸든 맞았든 정리
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Position:
    """열려 있는 포지션 하나."""
    receipt_id: str
    bot_id: str
    ticker: str
    qty: int                    # 마이크로 단위 수량
    basis: int                  # 투입한 USDC (마이크로)
    entry_price: int            # 진입가 (마이크로 USDC)
    opened_at: float = field(default_factory=time.time)
    tx: str = ""

    def pnl_pct(self, current_price: int) -> float:
        """진입가 대비 수익률 (%)."""
        if not self.entry_price:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price * 100

    def held_hours(self) -> float:
        return (time.time() - self.opened_at) / 3600

    # [삭제됨 2026-08] value() — 호출자가 없었다.
    # 평가금액이 필요한 곳(settle의 proceeds)은 같은 식을 직접 쓰고 있고,
    # 청산 판단은 pnl_pct()가 한다.


def should_close(pos: Position, current_price: int, rulebook) -> tuple[bool, str]:
    """청산할지 판정한다. (청산여부, 사유)

    손절을 가장 먼저 검사한다. 익절과 손절 조건이 동시에 성립할 수는
    없지만, 순서를 명시해두는 편이 나중에 조건이 늘어날 때 안전하다.
    """
    pct = pos.pnl_pct(current_price)

    if pct <= -abs(rulebook.stop_loss_pct):
        return True, f"stop_loss ({pct:+.2f}% ≤ -{rulebook.stop_loss_pct}%)"
    if pct >= abs(rulebook.take_profit_pct):
        return True, f"take_profit ({pct:+.2f}% ≥ +{rulebook.take_profit_pct}%)"
    held = pos.held_hours()
    if held >= rulebook.max_hold_hours:
        return True, f"max_hold ({held:.1f}h ≥ {rulebook.max_hold_hours}h)"
    return False, f"holding ({pct:+.2f}%, {held:.1f}h)"


class PositionBook:
    """열린 포지션 장부. 봇별로 조회할 수 있다."""

    def __init__(self) -> None:
        self._positions: dict[str, Position] = {}

    def open(self, pos: Position) -> None:
        self._positions[pos.receipt_id] = pos

    def close(self, receipt_id: str) -> Position | None:
        return self._positions.pop(receipt_id, None)

    def get(self, receipt_id: str) -> Position | None:
        return self._positions.get(receipt_id)

    def of_bot(self, bot_id: str) -> list[Position]:
        return [p for p in self._positions.values() if p.bot_id == bot_id]

    def all(self) -> list[Position]:
        return list(self._positions.values())

    def exposure(self, bot_id: str) -> int:
        """이 봇이 현재 시장에 묶어둔 총액. 추가 매수 판단에 쓴다."""
        return sum(p.basis for p in self.of_bot(bot_id))

    def __len__(self) -> int:
        return len(self._positions)


BOOK = PositionBook()
