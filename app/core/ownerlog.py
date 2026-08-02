"""소유자 관제용 감사 로그 — 지워져도 남는 기록.

/owner 의 표는 **지금 있는 봇**만 보여준다. 심사위원이 봇을 만들었다가
지우면 그 흔적이 통째로 사라져서, 나중에 보면 "아무도 안 왔다" 와
구분되지 않는다. 그래서 일어난 일을 따로 append-only 로 남긴다.

[왜 별도 모듈인가]
JOURNAL 은 봇 단위 기록이라 봇이 지워지면 같이 지워진다(그게 맞다 —
화면이 없는 봇의 거래내역을 보여줄 일이 없다). 이 로그는 반대로
**봇이 사라진 뒤가 본론**이라 수명이 다르다.

[한도]
메모리에 최근 MAX 건만 들고 있다. 심사 하루치로는 넉넉하고, 무한히
쌓여 메모리를 먹는 사고를 막는다. 넘치면 오래된 것부터 버린다.

⚠️ 호스팅에 영구 디스크를 안 붙였다면 **재배포 때 함께 사라진다.**
   컨테이너가 새로 뜨면 파일시스템이 초기화되기 때문이다.
   deploy/README.md 참조.
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import threading
import time
from collections import deque

MAX = 3000

# [왜 상태 파일에 얹지 않고 따로 쓰나]
# 처음엔 store.save() 가 남기는 app_state.json 에 같이 넣었다. 그런데
# save() 는 상태를 바꾸는 라우트에서만 불린다 — 대화나 실행처럼 저장을
# 유발하지 않는 일은 기록이 파일에 닿기 전에 프로세스가 죽으면 사라졌다.
# 실제로 9건 중 3건이 재시작에서 날아갔다.
#
# 그래서 한 줄씩 덧붙이는 파일을 따로 둔다. 이벤트 하나 = 한 줄이라
# 통째로 다시 쓸 일이 없고, 저장 시점이 곧 발생 시점이다.
LOG_PATH = pathlib.Path(os.environ.get(
    "OWNER_LOG_FILE",
    f"state/owner_log.{os.environ.get('LEDGER_MODE', 'mock').lower()}.jsonl"))


def mask(session: str) -> str:
    """세션을 식별은 되되 사칭은 못 하게. app/owner.py 와 같은 규칙."""
    if not session:
        return "공용"
    return hashlib.sha256(session.encode()).hexdigest()[:6]


class OwnerLog:
    def __init__(self) -> None:
        self._items: deque[dict] = deque(maxlen=MAX)
        self._lock = threading.Lock()
        self._seq = 0

    def add(self, kind: str, *, session: str = "", bot_id: str = "",
            name: str = "", detail: str = "", **extra) -> None:
        """일어난 일 하나. 실패해도 절대 위로 던지지 않는다 —
        기록을 남기려다 거래를 깨뜨리면 본말전도다."""
        try:
            with self._lock:
                self._seq += 1
                row = {
                    "seq": self._seq, "ts": time.time(), "kind": kind,
                    "session": mask(session), "bot_id": bot_id,
                    "name": name, "detail": detail, **extra,
                }
                self._items.append(row)
                self._append(row)
        except Exception:                          # noqa: BLE001, S110
            pass

    def _append(self, row: dict) -> None:
        """한 줄 덧붙인다. 파일이 안 써져도 메모리 기록은 살아 있어야 하므로
        여기서 난 예외는 삼킨다 — 관제 로그 때문에 거래가 멈추면 안 된다."""
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:                          # noqa: BLE001, S110
            pass

    def restore(self) -> int:
        """기동할 때 파일에서 되살린다. 최근 MAX 줄만 본다.

        깨진 줄은 조용히 건너뛴다 — 프로세스가 쓰는 도중에 죽으면
        마지막 줄이 잘려 있을 수 있는데, 그것 하나 때문에 나머지
        기록을 통째로 버릴 이유가 없다.
        """
        if not LOG_PATH.exists():
            return 0
        rows = []
        try:
            with LOG_PATH.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:                          # noqa: BLE001
            return 0
        with self._lock:
            self._items = deque(rows[-MAX:], maxlen=MAX)
            self._seq = max((r.get("seq", 0) for r in self._items), default=0)
            return len(self._items)

    def recent(self, limit: int = 200, kind: str = "") -> list[dict]:
        with self._lock:
            rows = list(self._items)
        if kind:
            rows = [r for r in rows if r["kind"] == kind]
        return rows[-limit:][::-1]                 # 최신이 위로

    def counts(self) -> dict[str, int]:
        with self._lock:
            rows = list(self._items)
        out: dict[str, int] = {}
        for r in rows:
            out[r["kind"]] = out.get(r["kind"], 0) + 1
        return out

    def sessions_seen(self) -> int:
        """지금까지 다녀간 브라우저 수 — 봇을 지운 사람까지 포함한다."""
        with self._lock:
            return len({r["session"] for r in self._items
                        if r["session"] != "공용"})

    def dump(self) -> dict:
        with self._lock:
            return {"seq": self._seq, "items": list(self._items)}

    def load(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            self._items = deque(data.get("items", []), maxlen=MAX)
            self._seq = int(data.get("seq", len(self._items)))


OWNER_LOG = OwnerLog()

# 화면에 한국어로 찍을 이름. 모르는 종류는 그대로 보여준다.
LABELS = {
    "bot_created": "봇 생성",
    "bot_deleted": "봇 삭제",
    "run": "봇 실행",
    "fill": "체결",
    "sell": "매도·정산",
    "chat": "대화",
    "wallet_connected": "지갑 연결",
    "deposit": "테스트 USDC 지급",
    "delegate": "자동결제 위임",
}
