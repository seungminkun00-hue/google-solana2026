"""사이클 진행 상황 중계.

[왜 필요한가 — 2026-08-03]
`POST /bots/{id}/cycle` 은 단계 로그를 차곡차곡 쌓아 **다 끝난 뒤에**
한 번에 돌려준다. mock 에서는 1초 만에 끝나니 문제가 안 보였다.
그런데 devnet 사이클은 온체인 트랜잭션 8~10건에 Gemini 호출 2번이라
30~120초가 걸린다. 그동안 화면에는 아무것도 안 뜬다 — 사용자가 보기에는
"버튼을 눌렀는데 아무 일도 안 일어나는" 것과 구분이 안 된다.

여기 쌓아두면 화면이 짧은 주기로 긁어가서 단계가 나오는 대로 보여준다.
사이클 코드는 손대지 않는다 — main.bot_cycle 이 쓰는 log 리스트가
append 될 때 이리로 흘려보낸다(_ProgressLog).

[무엇을 하지 않나]
저장하지 않는다. 진행 중인 것을 보여주는 용도라 프로세스 메모리면 충분하고,
서버가 재시작되면 진행 중이던 사이클도 함께 사라지므로 남길 이유가 없다.
"""
from __future__ import annotations

import time

# 봇 하나가 들고 있을 최근 단계 수. 사이클 하나가 10단계 남짓이라
# 넉넉하고, 봇이 많아도 메모리가 늘어날 여지가 없다.
MAX_STEPS = 60


class Progress:
    def __init__(self) -> None:
        # bot_id → {"seq": int, "steps": [{"n":…, "ts":…, **step}], "busy": bool}
        self._by_bot: dict[str, dict] = {}

    def _slot(self, bot_id: str) -> dict:
        return self._by_bot.setdefault(
            bot_id, {"seq": 0, "steps": [], "busy": False, "label": ""})

    def start(self, bot_id: str, label: str = "") -> int:
        """사이클 시작. 이전 진행분은 비운다 — 지난번 단계가 섞이면
        화면이 방금 일어난 일과 지난 일을 구분하지 못한다.
        돌려주는 seq 를 화면이 since 의 출발점으로 쓴다."""
        s = self._slot(bot_id)
        s["steps"].clear()
        s["busy"] = True
        s["label"] = label
        return s["seq"]

    def push(self, bot_id: str, step: dict) -> None:
        s = self._slot(bot_id)
        s["seq"] += 1
        s["steps"].append({"n": s["seq"], "ts": time.time(), **step})
        del s["steps"][:-MAX_STEPS]

    def done(self, bot_id: str) -> None:
        self._slot(bot_id)["busy"] = False

    def since(self, bot_id: str, n: int) -> dict:
        """n 번 이후에 생긴 단계만. 화면이 이걸 반복해서 부른다."""
        s = self._slot(bot_id)
        return {"seq": s["seq"], "busy": s["busy"], "label": s["label"],
                "steps": [x for x in s["steps"] if x["n"] > n]}


PROGRESS = Progress()


class ProgressLog(list):
    """append 될 때마다 PROGRESS 로 흘려보내는 목록.

    main.bot_cycle 의 `log` 를 이걸로 바꾸면 그 안의 log.append 15군데를
    하나도 손대지 않고 실시간 중계가 된다. list 그대로라 반환값·순회·
    len() 등 기존 동작은 전부 같다.
    """

    def __init__(self, bot_id: str) -> None:
        super().__init__()
        self.bot_id = bot_id

    def append(self, item) -> None:               # noqa: ANN001
        super().append(item)
        try:
            PROGRESS.push(self.bot_id, item)
        except Exception:                          # noqa: BLE001
            pass          # 중계가 사이클을 막으면 안 된다. 기록은 곁가지다.
