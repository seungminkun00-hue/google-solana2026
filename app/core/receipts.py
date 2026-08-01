"""의사결정 영수증 + 정산 + 봇 성적표 (Managed v2).

새로 추가: stats(bot_id) — 정산된 영수증에서 hit_rate와 avg_return을
계산한다. MVoT의 기대값이 상상이 아니라 온체인 기록에서 나오게 하는
핵심 함수. 표본 20건 미만이면 보수적 기본값 (콜드스타트 보호).
"""
from __future__ import annotations

import hashlib
import json
import time

from app import config
from app.core.ledger import LEDGER
from app.models import DecisionReceipt, PaymentProof


def h(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def effective_mode(declared: str, sources: dict[str, str]) -> tuple[str, bool]:
    """영수증에 박제할 '실제' 추론 출처를 정한다. (모드, 저하여부)

    [왜 필요한가]
    선언값(config.INFERENCE_MODE)을 그대로 박으면, Gemini 호출이 실패해
    모의 판단으로 폴백했을 때도 영수증은 "managed"라고 말한다.
    external.py의 폴백은 print 한 줄만 남기고 조용히 넘어가므로,
    그 테제는 '실제 추론'이라고 서명된 영수증을 달고 판매된다.
    파는 물건이 판단인 시스템에서 이건 그냥 거짓말이다.

    폴백 자체는 유지한다 — 추론 하나 못 받았다고 데모가 죽으면 안 된다.
    바꾸는 것은 '폴백했다는 사실을 숨기지 않는다'는 것뿐이다.
    """
    if declared != "managed":
        return declared, False
    if sources and all(v == "gemini-live" for v in sources.values()):
        return "managed", False
    return "degraded", True


class ReceiptStore:
    def __init__(self) -> None:
        self.receipts: dict[str, DecisionReceipt] = {}
        self.anchors: list[dict] = []

    def create(self, bot_id: str, source_urls: list[str], prompt: str, output: str,
               proofs: list[PaymentProof], policy_snapshot: dict,
               splits_bps: dict[str, int], declared_mode: str,
               inference_sources: dict[str, str]) -> DecisionReceipt:
        """의사결정 영수증을 만든다.

        declared_mode는 '무엇을 쓰려 했나'(config), inference_sources는
        '실제로 무엇이 답했나'(각 라우트의 source 필드)다.
        영수증에 박히는 것은 후자다 — effective_mode 주석 참조.
        """
        mode, degraded = effective_mode(declared_mode, inference_sources)
        r = DecisionReceipt(
            bot_id=bot_id,
            source_hashes=[h(u) for u in source_urls],
            prompt_hash=h(prompt),
            output_hash=h(output),
            x402_receipts=[p.proof_id for p in proofs],
            cognitive_cost=sum(p.amount for p in proofs),
            policy_snapshot=policy_snapshot,
            splits_bps=dict(splits_bps),               # 생성 시점 박제
            inference_mode=mode,
            inference_sources=dict(inference_sources),
            degraded=degraded,
            # [2026-08] degraded면 영수증을 '불완전'으로 표시해 판매를 막는다.
            #
            # 이 프로젝트에서 영수증은 "증명 가능성이 곧 판매 가능성"의
            # 근거다. managed로 선언하고 실제로는 모의 판단으로 폴백한
            # 결정은, 영수증이 뒷받침하지 못하는 물건이다.
            # 출처를 공개하는 것만으로는 부족하다 — 뒷받침 못 하는 물건은
            # 애초에 팔지 않는 것이 이 시스템의 주장과 맞다.
            #
            # 대가: managed 모드에서 Gemini가 실패하면 그 사이클의 테제는
            # 안 팔린다. 매매 자체는 그대로 진행되고(폴백 판단으로),
            # 기본 데모 경로(INFERENCE_MODE=mock)는 mode가 "mock"이라
            # 영향이 없다. 즉 시연이 멈추지는 않는다.
            receipt_complete=(mode != "byok" and not degraded),
        )
        self.receipts[r.receipt_id] = r
        return r

    def anchor(self, receipt_id: str) -> str:
        r = self.receipts[receipt_id]
        root = h(json.dumps(r.model_dump(), sort_keys=True, default=str))
        self.anchors.append({"receipt_id": receipt_id, "merkle_root": root,
                             "slot": len(self.anchors) + 1, "ts": int(time.time())})
        return root

    async def settle(self, receipt_id: str, realized_pnl: int,
                     invest_wallet: str) -> dict:
        r = self.receipts[receipt_id]
        if r.settled_at is not None:
            raise ValueError("이미 정산된 영수증")
        r.realized_pnl = realized_pnl

        legs_out: list[dict] = []
        if realized_pnl > 0:
            legs, distributed = [], 0
            items = list(r.splits_bps.items())
            for i, (wallet, bps) in enumerate(items):
                amt = (realized_pnl * bps) // 10_000
                if i == len(items) - 1:
                    amt = realized_pnl - distributed   # 잔차는 마지막 수취인
                distributed += amt
                legs.append((wallet, amt, f"settle:{receipt_id}"))
            proofs = await LEDGER.transfer_many(invest_wallet, legs)
            legs_out = [{"to": p.payee_wallet, "amount": p.amount} for p in proofs]

        r.settled_at = int(time.time())
        return {"receipt_id": receipt_id, "realized_pnl": realized_pnl,
                "distribution": legs_out,
                "roi_vs_cognitive_cost": (realized_pnl / r.cognitive_cost
                                          if r.cognitive_cost else None)}

    # ── 봇 성적표: MVoT의 입력이자 판매 신뢰의 근거 ────────────────
    def stats(self, bot_id: str, window: int = 100) -> dict:
        settled = [r for r in self.receipts.values()
                   if r.bot_id == bot_id and r.settled_at is not None][-window:]
        n = len(settled)
        if n < config.COLD_START_SAMPLES:
            return {"hit_rate": config.COLD_HIT_RATE,
                    "avg_return": config.COLD_AVG_RETURN,
                    "samples": n, "cold_start": True}
        wins = sum(1 for r in settled if (r.realized_pnl or 0) > 0)
        rets = [r.realized_pnl / r.position_size
                for r in settled if r.position_size]
        return {"hit_rate": wins / n,
                "avg_return": (sum(rets) / len(rets)) if rets else config.COLD_AVG_RETURN,
                "samples": n, "cold_start": False}


RECEIPTS = ReceiptStore()
