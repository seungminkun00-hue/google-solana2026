"""결제 증빙 관리 (nonce 저장소).

[감사 취약점 1 수정] 기존 paywall은 증빙 ID를 조회만 하고
소비 표시를 하지 않아, 한 번 결제한 증빙으로 무한 호출이 가능했다.
(감사 결과: 동일 증빙 5회 재사용 → 5회 모두 성공)

수정:
  1. 증빙에 resource를 바인딩 — 다른 엔드포인트에 재사용 불가
  2. 소비 시 consumed 표시 — 동일 엔드포인트에도 재사용 불가
  3. TTL 만료 — 오래된 증빙 거부 + 메모리 회수

온체인 대응: Solana에서는 트랜잭션 시그니처가 이미 1회성이지만,
'그 시그니처가 이 리소스에 대한 결제인지'는 별도 검증이 필요하다.
memo 필드에 resource 해시를 넣고 여기서 대조하는 것이 실전 방식.
"""
from __future__ import annotations

import threading
import time

TTL_SECONDS = 300  # 증빙 유효기간 5분


class ProofRejected(Exception):
    pass


class ProofRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}

    def register(self, proof_id: str, payee: str, amount: int, resource: str) -> None:
        with self._lock:
            self._records[proof_id] = {
                "payee": payee, "amount": amount, "resource": resource,
                "ts": time.time(), "consumed": False,
            }

    def consume(self, proof_id: str, payee: str, price: int, resource: str) -> None:
        """검증 + 소비를 원자적으로. 실패 시 ProofRejected."""
        with self._lock:
            rec = self._records.get(proof_id)
            if rec is None:
                raise ProofRejected("존재하지 않는 결제 증빙")
            if rec["consumed"]:
                raise ProofRejected("이미 사용된 증빙 (재사용 시도)")
            if time.time() - rec["ts"] > TTL_SECONDS:
                raise ProofRejected("만료된 증빙")
            if rec["payee"] != payee:
                raise ProofRejected("수취인 불일치")
            if rec["amount"] < price:
                raise ProofRejected(f"금액 부족: {rec['amount']} < {price}")
            if rec["resource"] != resource:
                raise ProofRejected(f"리소스 불일치: {rec['resource']} ≠ {resource}")
            rec["consumed"] = True

    def gc(self) -> int:
        """만료 레코드 정리. 메모리 무한 증가 방지."""
        with self._lock:
            cutoff = time.time() - TTL_SECONDS
            stale = [k for k, v in self._records.items() if v["ts"] < cutoff]
            for k in stale:
                del self._records[k]
            return len(stale)

    def stats(self) -> dict:
        with self._lock:
            return {"total": len(self._records),
                    "consumed": sum(1 for v in self._records.values() if v["consumed"])}


PROOFS = ProofRegistry()
