"""전체 시나리오 검증 — "이 프로젝트가 주장하는 것이 실제로 일어나는가".

개별 단위 테스트가 아니라, 트랙 요건과 핵심 주장을 처음부터 끝까지
실제로 실행해서 증명한다. 모든 판정은 실제 출력에 근거한다.

    $env:LEDGER_MODE="mock";   py -3.13 verify_scenario.py
    $env:LEDGER_MODE="devnet"; py -3.13 verify_scenario.py

devnet은 이체당 2~4초라 주기와 대기시간을 자동으로 늘린다 (약 8~12분).

[결정론 확보 방법]
시세는 난수(_BASE_PRICES ±3%)라 손익 부호가 매번 다르다. 그러면
"손실일 때 분배가 비어 있다" 같은 항목을 증명할 수 없다.
그래서 검증 중에는 모의 시장의 기준가를 직접 움직인다.
시스템의 판단을 조작하는 것이 아니라 '시장'을 움직이는 것이므로,
봇은 여전히 자기 룰북대로 반응한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time

MODE = os.environ.get("LEDGER_MODE", "mock").lower()
SLOW = MODE == "devnet"

# 검증 전용 상태 파일. 운영 상태와 섞이면 안 된다.
os.environ.setdefault("STATE_FILE", f"state/verify_{MODE}.json")
os.environ.setdefault("ADMIN_TOKEN", "dev-token")
os.environ.setdefault("INFERENCE_MODE", "mock")

STATE_FILE = pathlib.Path(os.environ["STATE_FILE"])
H = {"X-Admin-Token": os.environ["ADMIN_TOKEN"]}
BASE = "http://testserver"

SCHED_INTERVAL = 40 if SLOW else 3      # 스케줄러 주기
SCHED_TICKS = 2 if SLOW else 3          # 라이브 관찰 틱 수
MAX_CYCLES = 8 if SLOW else 14          # 기회를 잡을 때까지 최대 사이클


# ── 자식 프로세스 모드: 재시작 생존 확인 ────────────────────────────
def restore_check(bot_id: str) -> None:
    """새 프로세스에서 상태가 살아나는지 확인하고 JSON으로 보고한다.

    부모와 같은 프로세스에서 load()만 불러보면 메모리에 남아 있던
    값 때문에 통과해도 의미가 없다. 그래서 진짜로 프로세스를 나눈다.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).parent))
    from app.core.ledger import LEDGER            # noqa: F401  (순환 임포트 방지)
    # app.main 을 임포트해야 STORE.load() 가 돈다 — 복원은 앱 기동 시점에
    # 일어나므로, 이걸 빼면 빈 상태를 보고 "복원 실패"로 오판한다.
    import app.main                               # noqa: F401
    from app.bots import BOTS
    from app.core.positions import BOOK
    from app.core.receipts import RECEIPTS

    pos = BOOK.of_bot(bot_id)
    print("@@JSON@@" + json.dumps({
        "bot_alive": bot_id in BOTS,
        "rulebook": (BOTS[bot_id].rulebook.model_dump(mode="json")
                     if bot_id in BOTS else None),
        "positions": [{"receipt_id": p.receipt_id, "ticker": p.ticker,
                       "qty": p.qty, "basis": p.basis} for p in pos],
        "receipts": sum(1 for r in RECEIPTS.receipts.values()
                        if r.bot_id == bot_id),
        "ledger": type(LEDGER).__name__,
    }, ensure_ascii=False))


if "--restore-check" in sys.argv:
    restore_check(sys.argv[sys.argv.index("--restore-check") + 1])
    raise SystemExit(0)


# ── 본체 ────────────────────────────────────────────────────────────
sys.path.insert(0, str(pathlib.Path(__file__).parent))
STATE_FILE.parent.mkdir(exist_ok=True)
STATE_FILE.unlink(missing_ok=True)

import httpx                                                    # noqa: E402
from app import config, external                                # noqa: E402
from app.bots import BOTS                                       # noqa: E402
from app.core.ledger import LEDGER                              # noqa: E402
from app.core.mandate import issue_invoice                      # noqa: E402
from app.core.positions import BOOK, Position                   # noqa: E402
from app.core.receipts import RECEIPTS                          # noqa: E402
from app.core.routes import RouteViolation                      # noqa: E402
from app.core.scheduler import SCHEDULER                        # noqa: E402
from app.main import app                                        # noqa: E402

RESULTS: list[dict] = []
EVIDENCE: dict[str, str] = {}


def record(section: str, item: str, passed: bool | None, evidence: str) -> None:
    RESULTS.append({"section": section, "item": item,
                    "passed": passed, "evidence": evidence})
    mark = "✅" if passed else ("⏭️ " if passed is None else "❌")
    print(f"  {mark} [{section}] {item}")
    for line in str(evidence).splitlines():
        print(f"        {line}")


def head(title: str) -> None:
    print("\n" + "═" * 72)
    print(title)
    print("═" * 72)


def set_price(ticker: str, factor: float) -> int:
    """모의 시장의 기준가를 움직인다. 봇의 판단은 건드리지 않는다."""
    external._BASE_PRICES[ticker] = int(external._BASE_PRICES[ticker] * factor)
    return external._BASE_PRICES[ticker]


async def run_cycles(c, bot_id: str, want: str, limit: int) -> list[dict]:
    """원하는 step이 나올 때까지 사이클을 돌린다. 마지막 로그를 반환."""
    last: list[dict] = []
    for _ in range(limit):
        r = await c.post(f"/bots/{bot_id}/cycle", headers=H)
        if r.status_code != 200:
            return [{"step": "http-error", "detail": r.text[:200]}]
        last = r.json()["log"]
        if any(e.get("step") == want for e in last):
            return last
    return last


def step(log: list[dict], name: str) -> dict | None:
    return next((e for e in log if e.get("step") == name), None)


async def main() -> None:
    t0 = time.time()
    print("═" * 72)
    print(f" 전체 시나리오 검증   LEDGER_MODE={MODE}  "
          f"INFERENCE_MODE={config.INFERENCE_MODE}")
    print(f" 원장 구현: {type(LEDGER).__name__}   상태 파일: {STATE_FILE}")
    print("═" * 72)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 timeout=600) as c:

        # ── 준비: 데모봇 시드 ───────────────────────────────────
        r = await c.post("/demo/seed", headers=H)
        assert r.status_code == 200, r.text
        print(f"\n준비: 데모봇 시드 완료 {r.json()['seeded']}")

        # ══════════════════════════════════════════════════════
        head("C. 사용자 시나리오 — 봇 생성")
        # ══════════════════════════════════════════════════════
        r = await c.post("/bots", headers=H, json={
            "owner": "민수", "label": "시나리오 검증봇", "deposit_usdc": 200,
            "tickers": ["NVDA", "TSLA", "MSFT", "AAPL"],
            "min_confidence": 0.5, "max_position_usd": 30,
            "max_trades_per_day": 20,
            "take_profit_pct": 2.0, "stop_loss_pct": 2.0,
            "max_hold_hours": 99.0})
        assert r.status_code == 200, r.text
        d = r.json()
        BOT = d["bot_id"]
        record("C1", "룰북·예치금으로 봇 생성 (POST /bots)", True,
               f"bot_id={BOT}\n"
               f"룰북 {sorted(d['rulebook']['allowed_tickers'])} "
               f"확신도≥{d['rulebook']['min_confidence']}\n"
               f"잔고 {json.dumps(d['balances_usdc'], ensure_ascii=False)}")

        r2 = await c.post("/bots", headers=H, json={
            "owner": "테스트", "deposit_usdc": 10, "tickers": ["NOTREAL"]})
        record("C2", "존재하지 않는 종목 거부", r2.status_code == 400,
               f"HTTP {r2.status_code}  {json.dumps(r2.json(), ensure_ascii=False)[:150]}")

        # ══════════════════════════════════════════════════════
        head("B. 핵심 주장 — 에이전트가 자기 생각값을 스스로 번다")
        # ══════════════════════════════════════════════════════
        bal0 = await BOTS[BOT].balances()
        record("B1a", "research-agent가 0에서 시작", bal0["research-agent"] == 0.0,
               f"생성 직후 잔고: {json.dumps(bal0, ensure_ascii=False)}")

        # 사용자 원금에서 인지비용이 나가는 경로가 막히는가
        blocked = []
        for dst, why in [("external", "API 비용 직접 지불"),
                         (f"research-agent@{BOT}", "인지비용 원금 조달")]:
            try:
                await LEDGER.transfer(f"user-treasury@{BOT}", dst, 1_000, "attack")
                blocked.append(f"❌ {why}: 통과해버림")
            except RouteViolation as e:
                blocked.append(f"차단됨 ({why}): {e}")
        record("B2", "사용자 원금 → 인지비용 경로 차단",
               all("차단됨" in b for b in blocked), "\n".join(blocked))

        # ══════════════════════════════════════════════════════
        head("A. 트랙 요건 — 결제요청 생성 · 입금 · 정산")
        # ══════════════════════════════════════════════════════
        rev_before = (await LEDGER.snapshot()).get(f"revenue-wallet@{BOT}", 0)
        res_before = (await LEDGER.snapshot()).get(f"research-agent@{BOT}", 0)

        log = await run_cycles(c, BOT, "executor", MAX_CYCLES)
        bud, cap = step(log, "budget-check"), step(log, "capital-invoice")
        rep, ext = step(log, "replenish"), step(log, "external-sale")
        ex = step(log, "executor")

        record("A1a", "research-agent가 스스로 인보이스 발행",
               bud is not None and bud.get("amount", 0) > 0,
               f"budget-check → {json.dumps(bud, ensure_ascii=False)}")
        record("A1b", "invest-wallet이 스스로 자본 청구",
               cap is not None,
               f"capital-invoice → {json.dumps(cap, ensure_ascii=False)}")
        record("A1c", "사람 개입 지점 없음 (단일 POST /cycle 안에서 전부 발생)",
               True,
               f"한 번의 POST /bots/{BOT}/cycle 로그 순서:\n"
               f"  {[e.get('step') for e in log]}")
        record("A2a", "만다트 자동 승인 (입금)",
               bud is not None and bud.get("status") == "approved",
               f"인지 만다트: {bud.get('decided_reason') if bud else '없음'}\n"
               f"자본 만다트: {cap.get('decided_reason') if cap else '청구 불필요'}")

        # ── 만다트 자동 거절 3종 ────────────────────────────────
        bot = BOTS[BOT]
        rejects = []

        # (1) 성과 하한 미달
        inv = issue_invoice("capital", bot.w("invest-wallet"),
                            bot.w("user-treasury"), 0, 10 * config.USDC, 0, "저성과")
        r1 = await bot.cap_mandate.process(inv, hit_rate=0.30, cold_start=False,
                                           current_exposure=0)
        rejects.append(("성과 하한", r1.status.value, r1.decided_reason))

        # (2) 노출 한도 초과 — 실제 BOOK.exposure 기준
        BOOK.open(Position(receipt_id="verify_exposure", bot_id=BOT,
                           ticker="NVDAx", qty=1,
                           basis=config.CAP_MANDATE_MAX_EXPOSURE, entry_price=1))
        inv = issue_invoice("capital", bot.w("invest-wallet"),
                            bot.w("user-treasury"), 0, 10 * config.USDC, 0, "노출초과")
        r2m = await bot.cap_mandate.process(
            inv, hit_rate=0.9, cold_start=False,
            current_exposure=BOOK.exposure(BOT))
        rejects.append(("노출 한도", r2m.status.value, r2m.decided_reason))
        BOOK.close("verify_exposure")

        # (3) 재원 부족 — 판매수익 지갑에 없는 돈은 못 준다
        huge = issue_invoice("cognitive", bot.w("research-agent"),
                             bot.w("revenue-wallet"), 0,
                             500 * config.USDC, 5000 * config.USDC, "재원초과")
        r3 = await bot.cog_mandate.process(huge)
        rejects.append(("재원/주간한도", r3.status.value, r3.decided_reason))

        all_rejected = all(s == "rejected" for _, s, _ in rejects)
        record("A2b", "만다트 자동 거절 (자동출금이 아니라는 증거)", all_rejected,
               "\n".join(f"{k:12s} → {s}: {why}" for k, s, why in rejects))

        # ── A3 정산: 이익 → 85/10/5 분배 ────────────────────────
        tk = ex["ticker"] if ex else "NVDAx"
        record("C4a", "룰북대로 매수 (executor 체결)", ex is not None,
               f"executor → {json.dumps(ex, ensure_ascii=False)}")

        pos = BOOK.of_bot(BOT)
        assert pos, "포지션이 열리지 않아 정산 검증 불가"
        before_price = external._BASE_PRICES[tk]
        set_price(tk, 1.15)          # 시장이 올랐다 → 이익 확정
        r = await c.post(f"/settle/{pos[0].receipt_id}", headers=H)
        gain = r.json()
        legs = gain.get("distribution", [])
        ratios = {l["to"].split("@")[0]: round(l["amount"] / gain["realized_pnl"], 4)
                  for l in legs} if gain.get("realized_pnl") else {}
        ok_split = (gain.get("realized_pnl", 0) > 0 and len(legs) == 3
                    and abs(ratios.get("user-treasury", 0) - 0.85) < 0.01)
        record("A3a", "실현이익이 사전 확정 비율(85/10/5)로 분배", ok_split,
               f"기준가 {before_price/1e6:.2f} → {external._BASE_PRICES[tk]/1e6:.2f}\n"
               f"realized_pnl={gain.get('realized_pnl')}\n"
               f"distribution={json.dumps(legs, ensure_ascii=False)}\n"
               f"실제 비율={ratios}")

        # 단일 트랜잭션 여부 (devnet만 증명 가능)
        settle_proofs = [p for p in LEDGER.proofs
                         if p.resource.startswith(f"settle:{pos[0].receipt_id}")]
        if SLOW:
            sigs = {p.proof_id.split("#")[0] for p in settle_proofs}
            record("A3b", "분배 3건이 단일 트랜잭션", len(sigs) == 1,
                   f"수취 {len(settle_proofs)}건이 공유하는 시그니처: {sigs}\n"
                   f"https://explorer.solana.com/tx/{list(sigs)[0]}?cluster=devnet"
                   if sigs else "시그니처 없음")
        else:
            record("A3b", "분배 3건이 단일 트랜잭션", None,
                   "mock 원장은 단일 락 안에서 처리 — 시그니처가 없어 "
                   "온체인 증명 불가. devnet 실행에서 확인할 것.")

        # ── A3 정산: 손실 → 분배 없음 ───────────────────────────
        log = await run_cycles(c, BOT, "executor", MAX_CYCLES)
        ex2 = step(log, "executor")
        pos = BOOK.of_bot(BOT)
        if pos and ex2:
            tk2 = ex2["ticker"]
            set_price(tk2, 0.80)     # 시장이 떨어졌다 → 손실 확정
            r = await c.post(f"/settle/{pos[0].receipt_id}", headers=H)
            loss = r.json()
            record("A3c", "손실이면 분배가 비어 있다",
                   loss.get("realized_pnl", 1) < 0
                   and loss.get("distribution") == [],
                   f"realized_pnl={loss.get('realized_pnl')}  "
                   f"distribution={loss.get('distribution')}")
        else:
            record("A3c", "손실이면 분배가 비어 있다", None,
                   "두 번째 포지션이 열리지 않아 미검증")

        # ── B3 판매 수익이 인지비용을 충당하는가 ────────────────
        snap = await LEDGER.snapshot()
        rev_after = snap.get(f"revenue-wallet@{BOT}", 0)
        res_after = snap.get(f"research-agent@{BOT}", 0)
        inflow = [p for p in LEDGER.proofs
                  if p.payee_wallet == f"research-agent@{BOT}"]
        srcs = {p.payer_wallet.split("@")[0] for p in inflow}
        record("B1b", "research-agent 자금원이 인보이스뿐",
               srcs <= {"revenue-wallet", "invest-wallet"},
               f"입금 {len(inflow)}건의 출처: {srcs or '없음'}\n"
               f"(revenue-wallet=인보이스, invest-wallet=정산 분배분)\n"
               f"자원별 예시: {[p.resource for p in inflow[:3]]}")
        sales = [p for p in LEDGER.proofs
                 if p.payee_wallet == f"revenue-wallet@{BOT}"
                 and p.payer_wallet != "external" or
                 (p.payee_wallet == f"revenue-wallet@{BOT}"
                  and p.resource.startswith("ext-buy"))]
        spend = sum(p.amount for p in LEDGER.proofs
                    if p.payer_wallet == f"research-agent@{BOT}")
        earned = sum(p.amount for p in LEDGER.proofs
                     if p.payee_wallet == f"revenue-wallet@{BOT}"
                     and p.resource.startswith(("ext-buy", "settle")))
        record("B3", "판매 수익이 인지비용을 충당",
               earned >= spend and earned > 0,
               f"판매·정산으로 번 돈  {earned:>10,} µUSDC\n"
               f"인지비용 총지출      {spend:>10,} µUSDC\n"
               f"revenue-wallet {rev_before} → {rev_after}\n"
               f"research-agent {res_before} → {res_after}\n"
               f"external-sale 로그: {json.dumps(ext, ensure_ascii=False)}")

        # ══════════════════════════════════════════════════════
        head("C. 사용자 시나리오 — 룰북이 실제로 거부·청산하는가")
        # ══════════════════════════════════════════════════════
        # 매수 차단: 확신도 문턱을 올린다 (mock deep의 최대 확신도는 0.95)
        saved_conf = bot.rulebook.min_confidence
        bot.rulebook.min_confidence = 0.99
        log = await run_cycles(c, BOT, "rulebook", MAX_CYCLES)
        rb = step(log, "rulebook")
        record("C4b", "확신도 미달이면 매수하지 않는다",
               rb is not None and "blocked" in rb,
               f"min_confidence={bot.rulebook.min_confidence} 로 올린 뒤\n"
               f"rulebook → {json.dumps(rb, ensure_ascii=False)}\n"
               f"executor 단계 도달 여부: {step(log, 'executor') is not None}")
        bot.rulebook.min_confidence = saved_conf

        # 매도 발동: 손절선을 실제로 건드린다
        log = await run_cycles(c, BOT, "executor", MAX_CYCLES)
        ex3 = step(log, "executor")
        if ex3 and BOOK.of_bot(BOT):
            set_price(ex3["ticker"], 0.85)
            r = await c.post(f"/bots/{BOT}/manage-positions", headers=H)
            acts = r.json()["actions"]
            closed = [a for a in acts if a.get("closed")]
            record("C4c", "룰북 청산조건(손절)이 실제로 발동",
                   bool(closed),
                   f"기준가 -15% 이동 후 manage-positions\n"
                   f"{json.dumps(acts, ensure_ascii=False)[:300]}")
        else:
            record("C4c", "룰북 청산조건(손절)이 실제로 발동", None,
                   "포지션이 없어 미검증")

        # ══════════════════════════════════════════════════════
        head("C3. 스케줄러 — 사람 개입 없이 계속 도는가")
        # ══════════════════════════════════════════════════════
        # devnet에서는 데모봇까지 돌면 한 틱이 너무 길어진다.
        # 관찰 대상을 시나리오 봇으로 좁힌다 (데모봇은 뒤에서 다시 쓴다).
        paused = []
        if SLOW:
            for b in ("bot1", "bot2", "bot3"):
                BOTS[b].killed = True
                paused.append(b)

        r = await c.post("/scheduler/start", headers=H,
                         params={"interval_seconds": SCHED_INTERVAL})
        obs = []
        for i in range(SCHED_TICKS):
            await asyncio.sleep(SCHED_INTERVAL + (10 if SLOW else 1))
            st = (await c.get(f"/bots/{BOT}/state")).json()
            obs.append(f"[{(i+1)*SCHED_INTERVAL:>3}s] tick={SCHEDULER.tick_count} "
                       f"보유={st['open_positions']} 거래={st['trades_today']} "
                       f"결정={st['decisions']} 정산={st['settled']}")
        await c.post("/scheduler/stop", headers=H)
        for b in paused:
            BOTS[b].killed = False
        record("C3", "스케줄러가 사람 개입 없이 순회", SCHEDULER.tick_count > 0,
               f"주기 {SCHED_INTERVAL}s, 관찰 {SCHED_TICKS}틱 (사람 입력 0회)\n"
               + "\n".join(obs))

        # ══════════════════════════════════════════════════════
        head("D. 안전장치")
        # ══════════════════════════════════════════════════════
        # D2 봇 격리
        await c.post(f"/admin/kill/bot1", headers=H, params={"on": True})
        r1c = await c.post("/bots/bot1/cycle", headers=H)
        r2c = await c.post("/bots/bot2/cycle", headers=H)
        b1_blocked = any("blocked" in e for e in r1c.json().get("log", []))
        b2_ran = r2c.status_code == 200 and len(r2c.json().get("log", [])) > 1
        record("D2", "봇 하나가 정지해도 다른 봇은 계속 돈다",
               b1_blocked and b2_ran,
               f"bot1(정지) 로그: {json.dumps(r1c.json().get('log'), ensure_ascii=False)[:120]}\n"
               f"bot2(정상) 단계: {[e.get('step') for e in r2c.json().get('log', [])]}")
        record("D4", "킬 스위치가 즉시 먹는다", b1_blocked,
               f"POST /admin/kill/bot1 직후 첫 사이클이 차단됨")
        await c.post(f"/admin/kill/bot1", headers=H, params={"on": False})

        # D3 연속 실패 자동 정지
        #
        # 전용 봇을 따로 만든다. 시나리오 봇의 재원을 비우면 그 뒤의
        # C5(재시작 생존)에서 매수가 안 되어 포지션 0건으로 '공허하게'
        # 통과한다 — 실제로 그렇게 통과했다가 발견해서 분리했다.
        r = await c.post("/bots", headers=H, json={
            "owner": "실패검증", "label": "자동정지 확인용", "deposit_usdc": 5,
            "tickers": ["NVDA"], "min_confidence": 0.5,
            "max_position_usd": 5, "max_trades_per_day": 5})
        DRAIN = r.json()["bot_id"]
        # 재원을 허용된 경로로만 완전히 비운다 (화이트리스트를 우회하지 않음)
        snap = await LEDGER.snapshot()
        rv = snap.get(f"revenue-wallet@{DRAIN}", 0)
        if rv:
            await LEDGER.transfer(f"revenue-wallet@{DRAIN}",
                                  f"research-agent@{DRAIN}", rv, "verify:drain")
        snap = await LEDGER.snapshot()
        rs = snap.get(f"research-agent@{DRAIN}", 0)
        if rs:
            await LEDGER.transfer(f"research-agent@{DRAIN}", "external", rs,
                                  "verify:drain")

        # 틱마다 전 봇이 도는데 devnet에서는 그게 너무 느리다.
        # 나머지를 잠시 멈춰 이 봇만 실패시킨다.
        others = [b for b in BOTS if b != DRAIN]
        prev_killed = {b: BOTS[b].killed for b in others}
        for b in others:
            BOTS[b].killed = True
        SCHEDULER.errors.clear()
        for _ in range(6):                 # 대기 없이 틱을 직접 구동
            await SCHEDULER._tick()
        for b in others:
            BOTS[b].killed = prev_killed[b]

        killed = BOTS[DRAIN].killed
        record("D3", "연속 실패 시 그 봇만 자동 정지",
               killed and SCHEDULER.errors.get(DRAIN, 0) >= 5,
               f"전용 봇 {DRAIN} 의 재원을 비운 뒤 틱 6회 직접 구동\n"
               f"연속 실패 {SCHEDULER.errors.get(DRAIN)}회 → killed={killed}\n"
               f"시나리오 봇 {BOT} killed={BOTS[BOT].killed} "
               f"(실패 {SCHEDULER.errors.get(BOT, 0)}회)")

        # D6 통화량 보존
        aud = await LEDGER.audit_supply()
        record("D6", "통화량 보존", aud["conserved"],
               f"{json.dumps(aud, ensure_ascii=False)}")

        # D5 정산 재시도 이중 집행 방지 (devnet만 실증 가능)
        if SLOW:
            real = LEDGER._signature_landed
            sent: list[str] = []
            real_send = LEDGER.client.send_transaction
            calls = {"n": 0}

            async def spy(tx, *a, **k):
                sent.append(str(tx.signatures[0]))
                return await real_send(tx, *a, **k)

            async def flaky(sig):
                calls["n"] += 1
                if calls["n"] <= 6:
                    return None                # 429 상황: '모름'
                return await real(sig)

            snap = await LEDGER.snapshot()
            before = snap.get("revenue-wallet@bot1", 0)
            LEDGER.client.send_transaction = spy
            LEDGER._signature_landed = flaky
            try:
                sig = await LEDGER._send_tx(
                    [LEDGER._transfer_ix("external", "revenue-wallet@bot1", 1_000)],
                    [LEDGER.fee_payer, LEDGER.keypairs["external"]], label="verify")
            finally:
                LEDGER.client.send_transaction = real_send
                LEDGER._signature_landed = real
            await asyncio.sleep(6)
            LEDGER._invalidate_snapshot()
            after = (await LEDGER.snapshot()).get("revenue-wallet@bot1", 0)
            record("D5", "확정 조회가 실패해도 이체는 1회만 집행",
                   after - before == 1_000 and len(set(sent)) == 1 and len(sent) >= 2,
                   f"확정 조회 6회 강제 실패 → 같은 tx {len(sent)}회 재전송\n"
                   f"서로 다른 서명 수: {len(set(sent))}개 (1이어야 함)\n"
                   f"잔고 {before} → {after} (증가 {after - before}, 의도 1000)\n"
                   f"확정 서명: {sig}")
        else:
            record("D5", "확정 조회가 실패해도 이체는 1회만 집행", None,
                   "mock 원장은 네트워크·재시도가 없어 해당 없음. "
                   "devnet 실행에서 확인할 것.")

        # ══════════════════════════════════════════════════════
        head("C5. 재시작 생존 — 진짜로 프로세스를 나눈다")
        # ══════════════════════════════════════════════════════
        BOTS[BOT].killed = False
        # 살아있는 포지션을 하나 만들어 둔다.
        # 포지션이 0이면 "0건 → 0건 일치"로 공허하게 통과하므로,
        # 아래에서 포지션 개수가 0이면 통과로 치지 않는다.
        log = await run_cycles(c, BOT, "executor", MAX_CYCLES)
        want_pos = [{"receipt_id": p.receipt_id, "ticker": p.ticker,
                     "qty": p.qty, "basis": p.basis} for p in BOOK.of_bot(BOT)]
        want_rb = BOTS[BOT].rulebook.model_dump(mode="json")
        bal_before = await BOTS[BOT].balances()

    # 자식 프로세스 기동 (부모의 httpx 세션을 닫은 뒤)
    child = subprocess.run(
        [sys.executable, __file__, "--restore-check", BOT],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env={**os.environ}, cwd=str(pathlib.Path(__file__).parent))
    payload = next((l[8:] for l in (child.stdout or "").splitlines()
                    if l.startswith("@@JSON@@")), None)
    if payload is None:
        record("C5", "서버 재시작 후 봇·자금 생존", False,
               f"자식 프로세스 실패:\n{(child.stderr or child.stdout)[-400:]}")
    else:
        got = json.loads(payload)
        same_rb = (sorted(got["rulebook"]["allowed_tickers"])
                   == sorted(want_rb["allowed_tickers"])) if got["rulebook"] else False
        # 포지션 0건이면 "일치"가 공허하게 참이 된다. 그건 통과가 아니다.
        same_pos = bool(want_pos) and got["positions"] == want_pos
        vacuous = "  ← 포지션 0건은 공허한 통과라 실패로 처리" if not want_pos else ""
        record("C5", "서버 재시작 후 봇·자금 생존",
               got["bot_alive"] and same_rb and same_pos,
               f"새 프로세스(PID 다름)에서 로드한 결과\n"
               f"  원장={got['ledger']}  봇 생존={got['bot_alive']}\n"
               f"  룰북 일치={same_rb}  영수증={got['receipts']}건\n"
               f"  포지션 {len(want_pos)}건 → {len(got['positions'])}건, "
               f"일치={same_pos}{vacuous}\n"
               f"  {json.dumps(got['positions'], ensure_ascii=False)}\n"
               f"  재시작 전 잔고: {json.dumps(bal_before, ensure_ascii=False)}")

    # ══════════════════════════════════════════════════════════
    head("D1. audit.py 보안 감사")
    # ══════════════════════════════════════════════════════════
    env = {**os.environ, "LEDGER_MODE": "mock",
           "STATE_FILE": "state/verify_audit.json"}
    a = subprocess.run([sys.executable, "audit.py"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=env,
                       cwd=str(pathlib.Path(__file__).parent))
    lines = [l for l in (a.stdout or "").splitlines() if l.strip()[:1].isdigit()]
    ok_n = sum(1 for l in lines if "✅" in l)
    record("D1", f"audit.py 전 항목 통과", ok_n == len(lines) and ok_n >= 8,
           f"(LEDGER_MODE=mock 고정 — 보안 감사는 원장 구현과 무관)\n"
           + "\n".join(lines))

    # ══════════════════════════════════════════════════════════
    head("결과 요약")
    # ══════════════════════════════════════════════════════════
    passed = sum(1 for r in RESULTS if r["passed"] is True)
    failed = [r for r in RESULTS if r["passed"] is False]
    skipped = [r for r in RESULTS if r["passed"] is None]
    for r in RESULTS:
        mark = "✅" if r["passed"] else ("⏭️ " if r["passed"] is None else "❌")
        print(f"  {mark} {r['section']:5s} {r['item']}")
    print(f"\n  통과 {passed} / 실패 {len(failed)} / 미해당 {len(skipped)}"
          f"   소요 {time.time()-t0:.0f}초")
    if failed:
        print("\n  ❌ 실패 항목")
        for r in failed:
            print(f"     {r['section']} {r['item']}")
            for line in r["evidence"].splitlines():
                print(f"        {line}")

    out = pathlib.Path(f"state/verify_result_{MODE}.json")
    out.write_text(json.dumps(
        {"mode": MODE, "ledger": type(LEDGER).__name__,
         "passed": passed, "failed": len(failed), "skipped": len(skipped),
         "results": RESULTS}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n  결과 저장 → {out}")
    sys.exit(1 if failed else 0)


asyncio.run(main())
