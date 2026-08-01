"""보안 감사 v2 — 8개 항목."""
import asyncio, os, pathlib
os.environ.setdefault("ADMIN_TOKEN", "dev-token")
os.environ.setdefault("STATE_FILE", "state/test_audit.json")   # 운영 상태와 분리
pathlib.Path(os.environ["STATE_FILE"]).unlink(missing_ok=True)
import httpx

from app.main import app, SIGNALS, THESES
from app.bots import BOTS
from app.core.ledger import LEDGER, RouteViolation
from app.core.mandate import issue_invoice

H = {"X-Admin-Token": "dev-token"}


async def main():
    t = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=t, base_url="http://testserver") as c:
        await c.post("/demo/seed", headers=H)
        for b in ("bot1", "bot2", "bot3"):
            await c.post(f"/bots/{b}/cycle", headers=H)
        print("=" * 62)

        # 1. 증빙 재사용
        sig = next((s for s in SIGNALS.values()), None)
        used = next(p for p in LEDGER.proofs if "/sell/signal" in p.resource)
        hits = 0
        for _ in range(5):
            rr = await c.post(f"/bots/{sig.bot_id}/sell/signal/{sig.signal_id}",
                              headers={"X-PAYMENT": used.proof_id})
            hits += rr.status_code == 200
        print(f"1. 증빙 재사용 5회 → 성공 {hits}회 " + ("✅" if hits == 0 else "❌"))

        # 2. 봇 교차 결제 (bot1 증빙으로 bot2 창구)
        rr = await c.post(f"/bots/bot2/sell/signal/{sig.signal_id}",
                          headers={"X-PAYMENT": used.proof_id})
        print(f"2. 타 봇 창구 교차 사용 → {rr.status_code} " + ("✅" if rr.status_code in (402, 404) else "❌"))

        # 3. 알파 유출
        th = next(iter(THESES.values()))
        print(f"3. 판매본 방향 유출 → {th.leaks_side()} " + ("✅" if not th.leaks_side() else "❌"))

        # 4. 통화량 보존
        a = await LEDGER.audit_supply()
        print(f"4. 통화량 보존 → {a['conserved']} " + ("✅" if a["conserved"] else "❌"))

        # 5. 무인증 관리자
        rr = await c.post("/admin/kill/bot1")
        print(f"5. 무인증 킬스위치 → {rr.status_code} " + ("✅" if rr.status_code == 403 else "❌"))

        # 5-b. 무인증 자금 이동 라우트 (2026-08 추가)
        #      receipt_id만 알면 남의 포지션을 강제 청산할 수 있었다.
        money_routes = [("/settle/rcpt_any", {}),
                        ("/bots/bot1/close-all", {}),
                        ("/bots/bot1/replenish", {}),
                        ("/bots/bot1/cycle", {})]
        codes = [(u, (await c.post(u)).status_code) for u, _ in money_routes]
        blocked = all(code == 403 for _, code in codes)
        print(f"5b. 무인증 자금 라우트 4종 → "
              f"{[c2 for _, c2 in codes]} " + ("✅ 전부 차단" if blocked else "❌"))

        # 6. 지갑 격리
        try:
            await LEDGER.transfer("user-treasury@bot1", "external", 1000, "attack")
            print("6. 사용자 원금 유출 → ❌")
        except RouteViolation:
            print("6. 사용자 원금 유출 → ✅ 차단")

        # 7. 자본 만다트 거절 조건 (권한 없는 지갑의 청구)
        bot = BOTS["bot1"]
        bad = issue_invoice("capital", "research-agent@bot1",
                            bot.w("user-treasury"), 0, 50_000_000, 0, "탈취 시도")
        res = await bot.cap_mandate.process(bad, hit_rate=0.9, cold_start=False,
                                            current_exposure=0)
        print(f"7. 권한 없는 자본 청구 → {res.status.value} "
              + ("✅ 거절" if res.status.value == "rejected" else "❌"))

        # 8. 성과 하한 거절
        bad2 = issue_invoice("capital", bot.w("invest-wallet"),
                             bot.w("user-treasury"), 0, 10_000_000,
                             5_000_000, "저성과 추가투입")
        res2 = await bot.cap_mandate.process(bad2, hit_rate=0.30, cold_start=False,
                                             current_exposure=0)
        ok = res2.status.value == "rejected" and "성과 하한" in res2.decided_reason
        print(f"8. 적중률 30% 봇의 자본 청구 → " + ("✅ 거절: " + res2.decided_reason[:30] if ok else "❌"))
        print("=" * 62)

asyncio.run(main())
