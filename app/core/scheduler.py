"""자동 실행 스케줄러 — 켜두면 알아서 돈다.

[이전 구조의 문제]
test_cycle.py는 사람이 명령을 쳐야 1회 실행되고 끝났다.
투자 봇의 핵심은 사람이 자는 동안에도 도는 것인데 그게 없었다.

[매 주기마다 하는 일]
    1. 열린 포지션 점검 → 청산 조건 맞으면 매도
    2. 일일 카운터 리셋 (날이 바뀌었으면)
    3. 새 기회 탐색 → 조건 맞으면 매수

[안전 설계]
    - 봇 하나가 터져도 다른 봇은 계속 돈다 (예외 격리)
    - 연속 실패가 쌓이면 그 봇만 자동 정지 (무한 재시도 방지)
    - 사용자 요청으로 언제든 정지 가능
    - 정지 사유가 기록되어 왜 멈췄는지 알 수 있다
"""
from __future__ import annotations

import asyncio
import time
import traceback

MAX_CONSECUTIVE_ERRORS = 5


class Scheduler:
    def __init__(self, interval_seconds: int = 300) -> None:
        self.interval = interval_seconds
        self.running = False
        self._task: asyncio.Task | None = None
        self.tick_count = 0
        self.started_at: float | None = None
        self.errors: dict[str, int] = {}          # bot_id → 연속 실패 횟수
        self.log: list[dict] = []                 # 최근 활동 (최대 200건)
        # 마지막 주기가 언제 돌았나. 화면의 상태 램프가 '다음 실행까지
        # 몇 초' 를 보여주려면 이게 있어야 한다. 없으면 켜져 있다는 것만
        # 알고 살아 있는지는 알 수 없다 — 멈춘 램프와 구분이 안 된다.
        self.last_tick_at: float | None = None

    # ── 제어 ────────────────────────────────────────────────────
    def start(self, interval_seconds: int | None = None) -> dict:
        if self.running:
            return {"status": "already_running", **self.status()}
        if interval_seconds:
            self.interval = interval_seconds
        self.running = True
        self.started_at = time.time()
        self._task = asyncio.create_task(self._loop())
        self._record("scheduler", "started", {"interval": self.interval})
        return {"status": "started", **self.status()}

    def stop(self) -> dict:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self._record("scheduler", "stopped", {})
        return {"status": "stopped", **self.status()}

    def status(self) -> dict:
        uptime = time.time() - self.started_at if self.started_at else 0
        since = (time.time() - self.last_tick_at) if self.last_tick_at else None
        return {"running": self.running, "interval_seconds": self.interval,
                "ticks": self.tick_count, "uptime_seconds": round(uptime),
                "error_counts": dict(self.errors),
                "seconds_since_tick": round(since) if since is not None else None,
                # 다음 주기까지 남은 초. 램프 옆의 카운트다운이 이걸 쓴다.
                "next_tick_in": (max(0, round(self.interval - since))
                                 if self.running and since is not None else None)}

    def _record(self, who: str, event: str, detail: dict) -> None:
        self.log.append({"ts": time.time(), "who": who,
                         "event": event, "detail": detail})
        del self.log[:-200]      # 메모리 무한 증가 방지

    # ── 메인 루프 ───────────────────────────────────────────────
    async def _loop(self) -> None:
        while self.running:
            self.tick_count += 1
            self.last_tick_at = time.time()
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                # 루프 자체가 죽으면 자동화가 끝난다. 무슨 일이 있어도 살린다.
                self._record("scheduler", "tick_error", {"error": str(e)[:200]})
            await asyncio.sleep(self.interval)

    async def _tick(self) -> None:
        from app.bots import BOTS
        from app.core.proofs import PROOFS

        # 만료된 결제 증빙을 정리한다. ProofRegistry는 결제할 때마다
        # 레코드를 쌓기만 하고 비우는 곳이 없어서, 오래 돌릴수록 메모리가
        # 계속 늘었다. gc()는 TTL(300초)이 지난 것만 지우므로 진행 중인
        # 결제에는 영향이 없다. 주기마다 한 번이면 충분하다.
        PROOFS.gc()

        for bot_id, bot in list(BOTS.items()):
            if bot.killed:
                continue
            if self.errors.get(bot_id, 0) >= MAX_CONSECUTIVE_ERRORS:
                continue                       # 이미 자동 정지된 봇
            try:
                await self._run_bot(bot_id)
                self.errors[bot_id] = 0        # 성공하면 실패 카운터 초기화
            except Exception as e:
                n = self.errors.get(bot_id, 0) + 1
                self.errors[bot_id] = n
                self._record(bot_id, "error",
                             {"n": n, "error": str(e)[:200],
                              "trace": traceback.format_exc()[-300:]})
                if n >= MAX_CONSECUTIVE_ERRORS:
                    # 고장난 봇이 영원히 재시도하며 돈을 태우는 걸 막는다.
                    bot.killed = True
                    bot.tracker.policy.killed = True
                    self._record(bot_id, "auto_killed",
                                 {"reason": f"연속 실패 {n}회"})
                    # 정지 상태를 남긴다. 재시작하면 다시 살아나 같은
                    # 실패를 반복하는 것이 자동 정지의 취지를 무너뜨린다.
                    from app.core import store
                    store.save()

    async def _run_bot(self, bot_id: str) -> None:
        """봇 하나의 한 주기. 청산이 먼저, 매수가 나중."""
        import httpx
        from app.bots import BOTS
        from app.main import BASE, INTERNAL_HEADERS, app

        bot = BOTS[bot_id]
        if bot.roll_day():
            self._record(bot_id, "day_rolled", {})

        # 자금이 움직이는 라우트에 인증이 붙었다. 스케줄러는 이 프로세스
        # 내부이므로 내부 호출 헤더로 통과한다 — 관리자 토큰을 들고
        # 다니지 않아도 되고, 밖에서는 이 값을 알 수 없다.
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                     timeout=120,
                                     headers=INTERNAL_HEADERS) as c:
            # 1) 보유 포지션 점검이 항상 먼저다.
            #    새로 사기 전에 팔 것부터 파는 게 자금 효율에도 맞다.
            r = await c.post(f"{BASE}/bots/{bot_id}/manage-positions")
            if r.status_code == 200:
                acts = r.json().get("actions", [])
                for a in acts:
                    if a.get("closed"):
                        self._record(bot_id, "position_closed", a)

            # 2) 새 기회 탐색
            r = await c.post(f"{BASE}/bots/{bot_id}/cycle")
            if r.status_code == 200:
                steps = [e.get("step") for e in r.json().get("log", [])]
                self._record(bot_id, "cycle", {"steps": steps})
            else:
                raise RuntimeError(f"cycle {r.status_code}: {r.text[:150]}")


SCHEDULER = Scheduler()
