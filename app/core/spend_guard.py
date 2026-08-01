"""mainnet 총액 상한 — 진짜 돈에 대한 마지막 방어선.

devnet에서는 실수해도 0원이었다. mainnet은 다르다.
봇별 한도·일일 한도가 다 뚫려도 여기서 막힌다.

프로세스 전체에 걸리는 단일 카운터라, 봇을 몇 개 만들든
무한루프가 돌든 총 지출이 이 값을 넘을 수 없다.

    $env:MAINNET_TOTAL_CAP="1000000"   # $1.00 (기본값)
"""
from __future__ import annotations

import threading

from app import config


class MainnetCapExceeded(Exception):
    pass


class SpendGuard:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.spent = 0
        self.calls = 0
        self.log: list[tuple[str, int]] = []

    def check_and_record(self, label: str, amount: int) -> None:
        """결제 직전에 호출한다. 상한을 넘으면 예외로 막는다."""
        with self._lock:
            if self.spent + amount > config.MAINNET_TOTAL_CAP:
                raise MainnetCapExceeded(
                    f"mainnet 총액 상한 도달: "
                    f"{(self.spent + amount)/1e6:.4f} > "
                    f"{config.MAINNET_TOTAL_CAP/1e6:.4f} USD\n"
                    f"  누적 {self.calls}회 / ${self.spent/1e6:.4f}\n"
                    f"  계속하려면 MAINNET_TOTAL_CAP을 올리세요.")
            self.spent += amount
            self.calls += 1
            self.log.append((label, amount))

    def stats(self) -> dict:
        with self._lock:
            return {"spent_usd": round(self.spent / 1e6, 6),
                    "cap_usd": round(config.MAINNET_TOTAL_CAP / 1e6, 6),
                    "remaining_usd": round(
                        (config.MAINNET_TOTAL_CAP - self.spent) / 1e6, 6),
                    "calls": self.calls}


GUARD = SpendGuard()
