"""통합 테스트 v2: 봇 3개 사이클 + 안전장치 + 봇 간 시그널 거래."""
import asyncio, json, os, pathlib
os.environ.setdefault("ADMIN_TOKEN", "dev-token")
os.environ.setdefault("STATE_FILE", "state/test_cycle.json")   # 운영 상태와 분리
pathlib.Path(os.environ["STATE_FILE"]).unlink(missing_ok=True)
import httpx

from app.main import app, SIGNALS
from app.bots import BOTS
from app.core.ledger import LEDGER, RouteViolation

H = {"X-Admin-Token": "dev-token"}


async def main():
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://testserver") as c:

        print("═" * 62)
        print("① 시드: 봇 3개 각 $500 예치")
        r = await c.post("/demo/seed", headers=H)
        assert r.status_code == 200, r.text

        print("\n② 봇별 사이클")
        for bot_id in ("bot1", "bot2", "bot3"):
            r = await c.post(f"/bots/{bot_id}/cycle", headers=H)
            label = BOTS[bot_id].rulebook.label
            print(f"  ── {bot_id} ({label}) ──")
            for e in r.json()["log"]:
                step = e.pop("step")
                print(f"    [{step}] {json.dumps(e, ensure_ascii=False, default=str)[:100]}")

        print("\n③ 봇 간 시그널 거래 (bot2가 bot1 시그널 구매)")
        b1_sigs = [s for s in SIGNALS.values() if s.bot_id == "bot1"]
        if b1_sigs:
            from app.agents.pipeline import analyst_run
            try:
                th = await analyst_run(c, "http://testserver", BOTS["bot2"],
                                       b1_sigs[0].signal_id, seller_bot_id="bot1")
                print(f"    bot2 → bot1 시그널 $0.05 결제 → 테제 생성 ✅ ({th.ticker})")
            except Exception as e:
                print(f"    실패: {e}")
        else:
            print("    (bot1 시그널 없음 — SKIP만 나온 경우)")

        print("\n④ 대시보드")
        r = await c.get("/bots")
        for b in r.json()["bots"]:
            print(f"    {b['bot_id']} {b['label']:8s} 적중률 {b['hit_rate']} "
                  f"자급자족 {'✅' if b['self_funding'] else '❌'} "
                  f"revenue ${b['balances_usdc']['revenue-wallet']:.3f}")

        print("\n⑤ 전역 감사")
        g = (await c.get("/state")).json()
        print(f"    통화량 보존: {g['supply_audit']['conserved']}")
        print(f"    증빙: {g['proof_registry']}")

        print("\n⑥ 안전장치")
        await c.post("/admin/kill/bot1", headers=H)
        r = await c.post("/bots/bot1/cycle", headers=H)
        blocked = any("blocked" in e for e in r.json()["log"])
        print(f"    봇별 킬 스위치: {'✅' if blocked else '❌'}")
        r2 = await c.post("/bots/bot2/cycle", headers=H)
        b2_ok = not any("킬" in str(e.get("blocked", "")) for e in r2.json()["log"])
        print(f"    bot1 정지가 bot2에 영향 없음: {'✅' if b2_ok else '❌'}")
        try:
            await LEDGER.transfer("user-treasury@bot1", "external", 1000, "attack")
            print("    지갑 격리: ❌")
        except RouteViolation:
            print("    지갑 격리: ✅")
        r = await c.post("/admin/kill/bot1")
        print(f"    무인증 킬 시도: {'✅ 차단' if r.status_code == 403 else '❌'}")

asyncio.run(main())
