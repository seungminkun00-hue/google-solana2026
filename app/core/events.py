"""앱 상단 팝업 알림의 재료.

[왜 별도 버퍼인가]
저널(JOURNAL.fills)에도 체결이 다 남는다. 그런데 화면이 "새로 생긴 것"을
알려면 '지난번에 어디까지 봤는지'를 셀 수 있어야 한다. 저널은 시간순
목록이라 ts 로 비교하면 같은 초에 두 건이 들어올 때 하나를 놓친다.

그래서 단조 증가하는 seq 를 붙인 작은 링버퍼를 따로 둔다. 화면은 마지막
seq 만 기억하고 `/ui/events?since=` 로 그 뒤엣것만 받아간다.

이 버퍼는 저장하지 않는다. 재시작 후에 지난 체결 알림이 우르르 뜨는 것은
알림이 아니라 소음이고, 진짜 기록은 저널에 이미 있다.
"""
from __future__ import annotations

import threading
import time

MAX_EVENTS = 200


class EventLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._items: list[dict] = []

    def emit(self, kind: str, **payload) -> dict:
        with self._lock:
            self._seq += 1
            row = {"seq": self._seq, "kind": kind, "ts": time.time(), **payload}
            self._items.append(row)
            del self._items[:-MAX_EVENTS]
            return row

    def since(self, seq: int) -> list[dict]:
        with self._lock:
            return [e for e in self._items if e["seq"] > seq]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


EVENTS = EventLog()


def emit_fill(*, bot_id: str, ticker: str, side: str, qty: int,
              price_micro: int, gross_micro: int, pnl_micro: int | None,
              reason: str, tx: str) -> None:
    """체결 한 건을 알림으로 내보낸다. 실패해도 매매를 막지 않는다.

    봇 이름은 여기서 붙인다. 화면이 알림을 띄우는 순간 그 봇의 프로필을
    다시 조회하게 만들면, 알림 하나에 요청이 하나씩 더 붙는다.
    """
    try:
        from app.adapters import universe
        from app.core.markets import flag as market_flag
        from app.core.profiles import PROFILES
        prof = PROFILES.get(bot_id)
        EVENTS.emit(
            "fill",
            bot_id=bot_id,
            bot_name=(prof.display_name if prof and prof.display_name else bot_id),
            ticker=ticker,
            company=universe.company_name(ticker),
            flag=market_flag(ticker),
            side=side,
            qty=qty,
            price_micro=price_micro,
            gross_micro=gross_micro,
            pnl_micro=pnl_micro,
            reason=reason,
            tx=tx,
        )
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 체결 알림 실패: {str(e)[:80]}")


def emit_deposit(*, bot_id: str, amount_micro: int, tx: str,
                 source: str = "지갑") -> None:
    """입금 한 건을 알림으로 내보낸다.

    체결과 같은 자리에 뜬다. 사용자 입장에서 '돈이 움직였다'는 사건은
    매수든 충전이든 똑같이 즉시 보여야 하는 일이라, 알림 통로를
    따로 두지 않는다.
    """
    try:
        from app.core.profiles import PROFILES
        prof = PROFILES.get(bot_id)
        EVENTS.emit(
            "deposit",
            bot_id=bot_id,
            bot_name=(prof.display_name if prof and prof.display_name else bot_id),
            amount_micro=amount_micro,
            source=source,
            tx=tx,
        )
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 입금 알림 실패: {str(e)[:80]}")
