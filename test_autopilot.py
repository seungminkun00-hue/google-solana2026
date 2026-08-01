"""자동화 검증 — 사용자가 봇을 만들고, 스케줄러가 알아서 돌린다.

    py -3.13 test_autopilot.py
"""
import asyncio, json, os, pathlib
os.environ.setdefault("ADMIN_TOKEN", "dev-token")
# 운영 상태 파일과 분리한다. 공유하면 실행할 때마다 앞선 실행의 봇이
# 되살아나 누적되고, 이 테스트가 매번 다른 조건에서 돌게 된다.
os.environ.setdefault("STATE_FILE", "state/test_autopilot.json")
pathlib.Path(os.environ["STATE_FILE"]).unlink(missing_ok=True)
import httpx

from app.main import app
from app.core.positions import BOOK
from app.core.scheduler import SCHEDULER

H = {"X-Admin-Token": "dev-token"}


async def main():
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://testserver",
                                 timeout=180) as c:
        print("═" * 62)
        print("① 사용자가 앱에서 봇 생성")
        r = await c.post("/bots", headers=H, json={
            "owner": "민수", "label": "반도체 단타",
            "deposit_usdc": 300,
            "tickers": ["NVDA", "TSLA"],
            "min_confidence": 0.6,
            "max_position_usd": 40,
            "max_trades_per_day": 10,
            "take_profit_pct": 0.5,     # 데모용으로 좁게 → 청산이 빨리 일어남
            "stop_loss_pct": 0.5,
            "max_hold_hours": 0.05,  
        })
        assert r.status_code == 200, r.text
        d = r.json(); bot_id = d["bot_id"]
        print(f"   {bot_id} / {d['owner']}")
        rb = d["rulebook"]
        print(f"   매수: {sorted(rb['allowed_tickers'])} 확신도≥{rb['min_confidence']}")
        print(f"   매도: +{rb['take_profit_pct']}% / -{rb['stop_loss_pct']}% / "
              f"{rb['max_hold_hours']}h")
        print(f"   잔고: {json.dumps(d['balances_usdc'], ensure_ascii=False)}")

        print("\n② 잘못된 종목은 거부하는가")
        r = await c.post("/bots", headers=H, json={
            "owner": "테스트", "deposit_usdc": 10, "tickers": ["NOTREAL"]})
        print(f"   {r.status_code} {'✅ 거부' if r.status_code == 400 else '❌'}")

        print("\n③ 스케줄러 가동 (5초 주기)")
        r = await c.post("/scheduler/start", headers=H,
                         params={"interval_seconds": 60})
        print(f"   {r.json()['status']}  interval={r.json()['interval_seconds']}s")

        print("\n④ 25초 관찰 — 사람 개입 없음")
        for i in range(5):
            await asyncio.sleep(60)
            st = (await c.get(f"/bots/{bot_id}/state")).json()
            pos = BOOK.of_bot(bot_id)
            print(f"   [{(i+1)*5:>2}s] tick={SCHEDULER.tick_count}  "
                  f"보유={len(pos)}  거래={st['trades_today']}  "
                  f"결정={st['decisions']}  정산={st['settled']}")

        print("\n⑤ 스케줄러 활동 기록")
        for e in SCHEDULER.log[-8:]:
            det = json.dumps(e["detail"], ensure_ascii=False)[:70]
            print(f"   {e['who']:>14s}  {e['event']:<18s} {det}")

        print("\n⑥ 정지")
        r = await c.post("/scheduler/stop", headers=H)
        print(f"   {r.json()['status']}  총 {r.json()['ticks']}회 실행")

        print("\n⑦ 전체 청산")
        r = await c.post(f"/bots/{bot_id}/close-all", headers=H)
        print(f"   {r.json()['closed']}건 청산")

        st = (await c.get(f"/bots/{bot_id}/state")).json()
        print(f"\n⑧ 최종  결정 {st['decisions']}건 / 정산 {st['settled']}건 / "
              f"보유 {st['open_positions']}건")
        print(f"   잔고 {json.dumps(st['balances_usdc'], ensure_ascii=False)}")
        print("═" * 62)

asyncio.run(main())
