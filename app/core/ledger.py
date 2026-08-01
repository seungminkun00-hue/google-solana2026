"""원장 (Managed v2 — 멀티봇).

[async 통일] mock도 devnet도 전부 async 인터페이스를 쓴다.
mock은 즉시 끝나므로 async가 불필요해 보이지만, 두 구현의 모양이
같아야 호출하는 쪽이 "지금 뭐가 꽂혀 있는지" 몰라도 된다.
어댑터 교체가 한 줄로 끝나는 이유.

지갑 이름 규칙과 경로 화이트리스트는 app/core/routes.py 에 있다.
devnet 어댑터와 공유해야 하는데, 여기 두면 순환 임포트가 된다
(_make()가 devnet_ledger를 임포트하고 그쪽이 다시 여기를 임포트).
"""
from __future__ import annotations

import threading

from app.core.proofs import PROOFS
# 하위 호환 재수출 — 기존 `from app.core.ledger import RouteViolation` 등이
# 그대로 동작한다. 새 코드는 app.core.routes 에서 직접 가져오는 편이 낫다.
from app.core.routes import (ALLOWED_ROLE_ROUTES, InsufficientFunds,  # noqa: F401
                             RouteViolation, role)
from app.models import PaymentProof


class Ledger:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.balances: dict[str, int] = {
            "external": 10_000_000_000,
            "market": 10_000_000_000,
        }
        self.proofs: list[PaymentProof] = []
        self.total_supply = sum(self.balances.values())
        # 미러 주식 토큰 보유량: {"NVDAx": {"invest-wallet@bot1": 수량}}
        self.tokens: dict[str, dict[str, int]] = {}

    async def transfer(self, src: str, dst: str, amount: int,
                       resource: str) -> PaymentProof:
        with self._lock:
            return self._transfer_unlocked(src, dst, amount, resource)

    def _transfer_unlocked(self, src: str, dst: str, amount: int, resource: str) -> PaymentProof:
        if (role(src), role(dst)) not in ALLOWED_ROLE_ROUTES:
            raise RouteViolation(f"금지된 경로: {src} → {dst}")
        self.balances.setdefault(src, 0)
        self.balances.setdefault(dst, 0)
        if self.balances[src] < amount:
            raise InsufficientFunds(f"{src} 잔고 부족: {self.balances[src]} < {amount}")
        self.balances[src] -= amount
        self.balances[dst] += amount
        proof = PaymentProof(payer_wallet=src, payee_wallet=dst,
                             amount=amount, resource=resource)
        self.proofs.append(proof)
        PROOFS.register(proof.proof_id, dst, amount, resource)
        assert sum(self.balances.values()) == self.total_supply, "통화량 보존 위반"
        return proof

    async def transfer_many(self, src: str,
                            legs: list[tuple[str, int, str]]) -> list[PaymentProof]:
        """다자 원자적 분배. 전부 성공 or 전부 실패."""
        with self._lock:
            total = sum(a for _, a, _ in legs)
            if self.balances.get(src, 0) < total:
                raise InsufficientFunds(f"{src} 정산 재원 부족")
            for dst, _, _ in legs:
                if (role(src), role(dst)) not in ALLOWED_ROLE_ROUTES:
                    raise RouteViolation(f"정산 경로 위반: {src} → {dst}")
            return [self._transfer_unlocked(src, d, a, r) for d, a, r in legs]

    async def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.balances)

    # ── 미러 주식 스왑 ──────────────────────────────────────────
    async def swap_in(self, wallet: str, ticker: str,
                      usdc_amount: int, qty: int) -> PaymentProof:
        """USDC를 내고 주식 토큰을 받는다 (매수).

        ⚠️ 두 방향이 반드시 원자적이어야 한다.
           돈만 나가고 토큰을 못 받는 상태가 생기면 안 된다.
        """
        proof = await self.transfer(wallet, "market", usdc_amount, f"swap-in:{ticker}")
        with self._lock:
            self.tokens.setdefault(ticker, {})
            self.tokens[ticker][wallet] = self.tokens[ticker].get(wallet, 0) + qty
        return proof

    async def swap_out(self, wallet: str, ticker: str,
                       qty: int, usdc_amount: int) -> PaymentProof:
        """주식 토큰을 내고 USDC를 받는다 (매도)."""
        with self._lock:
            held = self.tokens.get(ticker, {}).get(wallet, 0)
            if held < qty:
                raise InsufficientFunds(f"{wallet} {ticker} 보유 부족: {held} < {qty}")
            self.tokens[ticker][wallet] = held - qty
        return await self.transfer("market", wallet, usdc_amount, f"swap-out:{ticker}")

    async def audit_supply(self) -> dict:
        current = sum(self.balances.values())
        return {"expected": self.total_supply, "actual": current,
                "conserved": current == self.total_supply}

    # ── 재시작 생존 ─────────────────────────────────────────────
    # devnet 원장에는 이 두 메서드가 없다. 필요가 없기 때문이다 —
    # 그쪽의 진실원은 온체인이고, 프로세스가 죽어도 잔고와 토큰이 남는다.
    # mock은 메모리가 진실원이라 스스로 남기지 않으면 사라진다.
    # 봇과 포지션만 복원하고 원장을 복원하지 않으면, 장부에는 포지션이
    # 있는데 원장에는 토큰이 없는 상태가 되어 청산이 불가능해진다.
    # (실측: 재시작 후 close-all → "NVDAx 보유 부족: 0 < 63334")
    def dump_state(self) -> dict:
        with self._lock:
            return {"balances": dict(self.balances),
                    "tokens": {t: dict(v) for t, v in self.tokens.items()},
                    "total_supply": self.total_supply}

    def load_state(self, data: dict) -> None:
        with self._lock:
            self.balances = {k: int(v) for k, v in data["balances"].items()}
            self.tokens = {t: {w: int(q) for w, q in v.items()}
                           for t, v in data.get("tokens", {}).items()}
            # 통화량 불변식의 기준도 함께 되살린다. 기본값으로 두면
            # 복원 직후 첫 이체에서 assert가 터진다.
            self.total_supply = int(data.get("total_supply",
                                             sum(self.balances.values())))


def _make() -> "Ledger":
    """LEDGER_MODE=devnet 이면 진짜 SPL 이체 어댑터로 교체한다.
    순환 임포트를 피하려고 여기서 지연 임포트한다."""
    import os
    if os.environ.get("LEDGER_MODE", "mock").lower() == "devnet":
        from app.adapters.devnet_ledger import DevnetLedger
        return DevnetLedger()
    return Ledger()


LEDGER = _make()
