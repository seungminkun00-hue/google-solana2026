"""봇 레지스트리 (Managed v2).

BotInstance 하나 = 사용자 한 명의 봇 하나.
지갑 4개, 지출정책, 만다트 2개, 룰북이 전부 봇 단위로 격리된다.
전역 싱글톤(감사 잔여 리스크 1번)이 이것으로 해소된다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from app import config
from app.core.ledger import LEDGER
from app.core.mandate import CapitalMandate, CognitiveMandate
from app.core.x402_client import SpendTracker
from app.models import Rulebook, SpendPolicy

USDC = config.USDC


@dataclass
class BotInstance:
    bot_id: str
    owner: str
    rulebook: Rulebook
    tracker: SpendTracker = field(init=False)
    cog_mandate: CognitiveMandate = field(default_factory=CognitiveMandate)
    cap_mandate: CapitalMandate = field(default_factory=CapitalMandate)
    trades_today: int = 0
    _trade_day: int = 0          # 일일 카운터를 리셋할 기준일
    killed: bool = False
    created_at: float = 0.0

    def __post_init__(self) -> None:
        self.created_at = self.created_at or time.time()
        self._trade_day = int(time.time() // 86_400)
        # 지갑 준비는 생성자에서 하지 않는다 (async라 부를 수도 없다).
        # devnet은 create_bot이 ensure_bot_wallets로 만들고,
        # mock은 첫 이체 시 자동 생성된다.
        self.tracker = SpendTracker(
            wallet=self.w("research-agent"),
            policy=SpendPolicy(
                daily_cap=config.RESEARCH_DAILY_CAP,
                per_decision_cap=config.RESEARCH_PER_DECISION_CAP,
                session_expires_at=int(time.time()) + 3600,
            ),
            auto_renew_seconds=3600,
        )

    def roll_day(self) -> bool:
        """날이 바뀌었으면 일일 거래 카운터를 리셋한다.

        ⚠️ 이게 없으면 봇이 하루치 한도를 다 쓴 뒤 영원히 멈춘다.
        1회성 테스트에서는 안 드러나지만, 계속 돌리는 순간 치명적이다.
        """
        today = int(time.time() // 86_400)
        if today != self._trade_day:
            self._trade_day = today
            self.trades_today = 0
            return True
        return False

    def w(self, role: str) -> str:
        return f"{role}@{self.bot_id}"

    @property
    def splits(self) -> dict[str, int]:
        """정산 분배표. 영수증 생성 시 이 스냅샷이 박제된다."""
        return {
            self.w("user-treasury"): config.SPLIT_USER_BPS,
            self.w("revenue-wallet"): config.SPLIT_REVENUE_BPS,
            self.w("research-agent"): config.SPLIT_RESEARCH_BPS,
        }

    async def balances(self) -> dict[str, float]:
        snap = await LEDGER.snapshot()
        return {r: snap.get(self.w(r), 0) / USDC
                for r in ("user-treasury", "invest-wallet", "research-agent", "revenue-wallet")}


BOTS: dict[str, BotInstance] = {}


def validate_rulebook(rb: Rulebook) -> list[str]:
    """룰북의 종목이 실제로 존재하는지 확인한다.

    smartmoney.market의 SEC 내부자 추적 목록(무료 라우트, 1,045종목)과
    대조한다. 존재하지 않는 종목만 넣으면 봇이 영원히 아무것도 못 사는데,
    사용자는 그 이유를 모른 채 "왜 안 사지?"라고만 생각하게 된다.
    """
    from app.adapters import universe
    bad = [t for t in rb.allowed_tickers if not universe.is_tracked(t)]
    return bad


DEMO_BOT_IDS = ("bot1", "bot2", "bot3")


def make_demo_bots() -> None:
    """시연용 봇 3개 — 서로 다른 룰북 = 서로 다른 전문 분야.

    [2026-08 수정] 예전에는 BOTS.clear()로 시작했다. 이 함수는
    /demo/seed가 부르므로, 시연 중 "초기화 한 번 더"를 누르는 순간
    사용자가 만든 봇이 통째로 사라졌다. devnet에서는 그 봇의 온체인
    자금까지 회수 불가 상태가 된다(지갑 매핑이 메모리에만 있으므로).

    이제 데모봇 3개만 다시 만들고 나머지는 손대지 않는다.
    """
    demo = [
        ("bot1", "민수", Rulebook(label="반도체 집중형",
                                  allowed_tickers={"NVDAx"}, min_confidence=0.85,
                                  max_position_usdc=80 * USDC)),
        ("bot2", "영희", Rulebook(label="분산 안정형",
                                  allowed_tickers={"NVDAx", "TSLAx", "MSFTx", "AAPLx"},
                                  min_confidence=0.6, max_position_usdc=40 * USDC)),
        ("bot3", "철수", Rulebook(label="빅테크 단일",
                                  allowed_tickers={"MSFTx"}, min_confidence=0.7,
                                  max_position_usdc=100 * USDC)),
    ]
    for bot_id, owner, rb in demo:
        bad = validate_rulebook(rb)
        if bad:
            print(f"  ⚠️ {bot_id} 룰북에 추적 대상이 아닌 종목: {bad}")
        BOTS[bot_id] = BotInstance(bot_id=bot_id, owner=owner, rulebook=rb)
