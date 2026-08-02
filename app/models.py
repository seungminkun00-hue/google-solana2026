"""도메인 모델 (Managed v2).

추가된 것:
  Rulebook       사용자가 쓰는 헌법. 선언형 JSON만 허용 (eval 금지).
  DecisionReceipt에 bot_id, position_size — 봇별 성적표 계산의 재료.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum

from pydantic import BaseModel, Field


def now() -> int:
    return int(time.time())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── 룰북: 사용자의 헌법 ─────────────────────────────────────────────
class Rulebook(BaseModel):
    """사용자가 정하는 헌법. LLM이 뭐라고 하든 최종 거부권은 여기 있다.

    보안: 반드시 선언형 필드만. 수식·코드 문자열을 받아 eval하면
    사용자가 서버에서 임의 코드를 실행할 수 있다. 절대 금지.
    """
    # ── 매수 조건 ──────────────────────────────────────────────
    allowed_tickers: set[str]           # 화이트리스트 (금지목록 아님)
    min_confidence: float = 0.8         # 이 확신도 미만이면 매수 안 함
    max_position_usdc: int = 100 * 1_000_000   # 1회 최대 투입
    max_trades_per_day: int = 5

    # ── 매도 조건 (자동 청산) ──────────────────────────────────
    # 사람이 안 보고 있을 때 손실을 끊고 이익을 확정하는 장치.
    # 이게 없으면 봇이 산 뒤 영원히 들고만 있는다.
    take_profit_pct: float = 5.0        # +5%면 익절
    stop_loss_pct: float = 3.0          # -3%면 손절
    max_hold_hours: float = 24.0        # 이 시간 넘으면 무조건 청산

    label: str = ""


# ── x402 결제 증빙 ──────────────────────────────────────────────────
class PaymentProof(BaseModel):
    proof_id: str = Field(default_factory=lambda: new_id("pay"))
    payer_wallet: str
    payee_wallet: str
    amount: int
    resource: str
    ts: int = Field(default_factory=now)


# ── 인보이스 ────────────────────────────────────────────────────────
class InvoiceStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Invoice(BaseModel):
    invoice_id: str = Field(default_factory=lambda: new_id("inv"))
    kind: str                        # "cognitive" | "capital"
    issuer: str                      # 청구하는 지갑
    payer: str                       # 지급하는 지갑
    amount: int
    reason: str
    expected_value: int = 0
    status: InvoiceStatus = InvoiceStatus.PENDING
    decided_reason: str = ""
    ts: int = Field(default_factory=now)

    @property
    def expected_roi(self) -> float:
        return self.expected_value / self.amount if self.amount else 0.0


# ── 의사결정 영수증 ─────────────────────────────────────────────────
class DecisionReceipt(BaseModel):
    receipt_id: str = Field(default_factory=lambda: new_id("rcpt"))
    bot_id: str = ""
    source_hashes: list[str] = []
    prompt_hash: str = ""
    output_hash: str = ""
    x402_receipts: list[str] = []
    cognitive_cost: int = 0
    policy_snapshot: dict = {}
    splits_bps: dict[str, int] = {}      # 실제 지갑ID → bps (생성 시 박제)
    position_size: int = 0
    execution_tx: str | None = None
    settled_at: int | None = None
    realized_pnl: int | None = None

    # ── 추론 출처 (영수증이 거짓말하지 않게 하는 부분) ──────────────
    # inference_mode는 '선언한 모드'가 아니라 '실제로 무엇이 판단했는지'다.
    # 예전에는 config.INFERENCE_MODE를 그대로 박았는데, Gemini 호출이
    # 실패해 모의 판단으로 폴백해도 영수증은 여전히 "managed"라고 말했다.
    # 그 영수증을 달고 테제가 팔리는 순간 사는 쪽을 속이는 것이 된다.
    #   mock     전부 모의 판단
    #   managed  전부 실제 Gemini
    #   degraded managed로 선언했으나 일부/전부가 폴백 (혼합 포함)
    inference_mode: str = "mock"
    inference_sources: dict[str, str] = {}   # {"flash": "...", "deep": "..."}
    degraded: bool = False                   # 선언과 실제가 어긋났는가
    receipt_complete: bool = True            # 폴백했으면 False → 판매 불가

    # ── 사람이 직접 시킨 주문인가 ────────────────────────────────────
    # 대화로 "지금 사줘" 라고 지시하면 룰북 게이트를 거치지 않고 바로
    # 집행된다. 그건 에이전트의 판단이 아니라 **소유자의 수동 주문**이다.
    #
    # 반드시 구분해서 남겨야 하는 이유가 둘이다.
    #   ① 그 체결을 근거로 "에이전트가 룰북을 넘어섰다"고 읽히면 안 된다.
    #      룰북은 여전히 에이전트의 자율 판단에만 걸리는 규칙이다.
    #   ② 적중률(RECEIPTS.stats)은 에이전트의 성적이다. 사람이 시킨
    #      주문의 손익이 섞이면 만다트가 잘못된 성적으로 심사하게 된다.
    manual: bool = False
    manual_reason: str = ""                  # 사용자가 실제로 한 말
    ts: int = Field(default_factory=now)


# ── 지출 정책 ───────────────────────────────────────────────────────
class SpendPolicy(BaseModel):
    daily_cap: int
    per_decision_cap: int
    session_expires_at: int
    killed: bool = False


# ── 산출물 ──────────────────────────────────────────────────────────
class Signal(BaseModel):
    signal_id: str = Field(default_factory=lambda: new_id("sig"))
    bot_id: str = ""
    ticker: str
    headline: str
    novelty: float
    relevance: float = 0.5           # Flash 스크리닝 점수
    source_hash: str
    ts: int = Field(default_factory=now)


class Thesis(BaseModel):
    thesis_id: str = Field(default_factory=lambda: new_id("ths"))
    bot_id: str = ""
    ticker: str
    side: str | None
    confidence: float
    rationale: str
    size_usdc: int | None
    price_micro: int = 0          # 체결 시점 시세 (마이크로 USDC)
    sanitized: bool = False
    receipt_id: str
    ts: int = Field(default_factory=now)

    def for_sale(self) -> "Thesis":
        masked = self.rationale
        for w in ("buy", "sell", "매수", "매도", "long", "short", "롱", "숏"):
            masked = masked.replace(w, "[REDACTED]")
        return self.model_copy(update={
            "side": None, "size_usdc": None, "sanitized": True, "rationale": masked})

    def leaks_side(self) -> bool:
        s = self.for_sale()
        return any(w in s.rationale.lower()
                   for w in ("buy", "sell", "매수", "매도", "long", "short"))
