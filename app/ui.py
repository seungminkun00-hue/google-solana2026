"""앱 화면 전용 라우터 (/ui/*).

[이 파일의 경계]
여기에는 **자금을 움직이는 코드가 한 줄도 없다.** 하는 일은 이미 일어난
사실(원장·포지션·영수증·저널)을 읽어 화면이 묻는 모양으로 접는 것뿐이다.
매매·정산·결제는 전부 main.py 의 기존 라우트가 그대로 담당한다.

그렇게 나눈 이유는, 화면을 고치다가 돈이 도는 경로를 건드리는 일이
없어야 하기 때문이다. verify_scenario.py 25항목과 audit.py 9항목이
검증하는 것은 저쪽이고, 이 파일은 그 위에 얹힌 유리창이다.

예외는 두 개다.
  POST /ui/bots            — 봇 생성. main.create_bot 을 그대로 부른다
                             (룰북 검증·지갑 생성·예치가 거기 있으므로
                              두 벌로 만들지 않는다).
  POST /ui/bots/{id}/pause — 킬 스위치. main.kill_bot 과 같은 일.

[숫자 규칙]
원장의 진실원은 마이크로 USDC 정수다. 나눗셈은 화면에 내보내기 직전에만
한다. 응답에는 항상 `_micro`(정수 원본)와 표시값을 같이 실어서, 프론트가
반올림을 다시 하다 어긋나는 일이 없게 한다.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app import config
from app.adapters import fx as FX
from app.core import markets as MARKETS_
from app.core.journal import JOURNAL, PROVIDERS, classify
from app.core.ledger import LEDGER
from app.core.markets import DEFAULT_KEYS as DEFAULT_MARKETS
from app.core.ownerlog import OWNER_LOG
from app.core.positions import BOOK
from app.core.profiles import (CURRENCIES, GOALS, MODELS, RISKS,
                               SESSIONS, STYLES, TAGS, BotProfile, PROFILES)
from app.core.receipts import RECEIPTS
from app.core.session import owns, session_id

router = APIRouter(prefix="/ui", tags=["ui"])

USDC = config.USDC
WALLET_ROLES = ("user-treasury", "invest-wallet", "research-agent",
                "revenue-wallet")
REGISTRY_PATH = pathlib.Path("wallets") / "registry.json"

# 도넛·범례 색. Figma node 1:649~1:653 의 범례 스와치 그대로이고,
# web/src/styles/tokens.css 의 --series-* 와 같은 순서·같은 값이다.
SERIES_COLORS = ["#0046ff", "#6b85c9", "#00b7ff", "#14317d", "#8ed0ea"]

_LEDGER_MODE = os.environ.get("LEDGER_MODE", "mock").lower()
EXPLORER = "https://explorer.solana.com/tx/{}?cluster=devnet"


def _explorer(tx: str) -> str | None:
    """이 서명을 Explorer 에서 볼 수 있는가.

    mock 원장의 증빙 ID(`pay_…`)는 온체인에 없다. 링크를 걸어두면
    누르는 사람은 '없는 트랜잭션'을 보게 되고, 그게 더 나쁘다.
    devnet 에서 나온 진짜 서명일 때만 링크를 만든다.
    """
    if not tx or _LEDGER_MODE != "devnet" or tx.startswith("pay_"):
        return None
    return EXPLORER.format(tx)


# ══════ 공통 계산 ══════════════════════════════════════════════════
async def _prices(tickers: set[str]) -> dict[str, int]:
    """이번 요청에서 쓸 시세를 종목당 한 번만 받아온다.

    live 모드에서 spot()은 pay CLI 서브프로세스라 비싸다. 같은 응답 안에서
    도넛·평가손익·거래요약이 각자 부르면 같은 종목을 여러 번 결제한다.
    """
    from app.external import spot
    return {t: await spot(t) for t in tickers}


async def _bot_valuation(bot, prices: dict[str, int] | None = None) -> dict:
    """이 봇의 현재 가치. 화면 거의 모든 숫자의 뿌리다.

      cash    네 지갑의 USDC 합
      market  열린 포지션의 평가액 (수량 × 현재가)
      basis   그 포지션들에 실제로 넣은 원가
    """
    snap = await LEDGER.snapshot()
    cash = sum(snap.get(bot.w(r), 0) for r in WALLET_ROLES)

    positions = BOOK.of_bot(bot.bot_id)
    if prices is None:
        prices = await _prices({p.ticker for p in positions})

    market = 0
    basis = 0
    for p in positions:
        market += p.qty * prices.get(p.ticker, p.entry_price) // USDC
        basis += p.basis

    return {
        "cash_micro": cash,
        "market_micro": market,
        "basis_micro": basis,
        "total_micro": cash + market,
        "unrealized_micro": market - basis,
        "positions": positions,
        "prices": prices,
        "per_wallet": {r: snap.get(bot.w(r), 0) for r in WALLET_ROLES},
    }


def _realized(bot_id: str) -> tuple[int, int]:
    """(실현손익 합, 총 매수 투입액 합).

    투입액은 저널의 매수 체결에서 센다. 다만 저널이 생기기 전에 열린
    포지션은 거기 없다 — 그런 포지션의 원가는 장부(BOOK)에서 직접
    가져와 더한다. 안 그러면 "포지션은 있는데 투입액이 0" 이 되어
    수익률이 통째로 사라진다.
    """
    fills = JOURNAL.fills_of(bot_id)
    pnl = sum(f["pnl_micro"] or 0 for f in fills if f["side"] == "sell")
    invested = sum(f["gross_micro"] for f in fills if f["side"] == "buy")

    journaled = {f["receipt_id"] for f in fills if f["side"] == "buy"}
    invested += sum(p.basis for p in BOOK.of_bot(bot_id)
                    if p.receipt_id not in journaled)
    return pnl, invested


def _return_pct(bot_id: str, val: dict) -> dict:
    """총 수익률.

        (실현손익 + 평가손익) ÷ 총 투입원가

    이렇게 정의하는 이유: 잔고 기준으로 재면 입출금만 해도 수익률이
    움직인다. 투입한 돈에 대해 얼마를 벌었는지가 이 봇의 성적이다.
    투입이 아직 0이면 수익률은 존재하지 않는다 — 0%가 아니라 None이다.
    """
    realized, invested = _realized(bot_id)
    unrealized = val["unrealized_micro"]
    if invested <= 0:
        return {"pct": None, "realized_micro": realized,
                "unrealized_micro": unrealized, "invested_micro": invested}
    return {
        "pct": round((realized + unrealized) / invested * 100, 2),
        "realized_micro": realized,
        "unrealized_micro": unrealized,
        "invested_micro": invested,
    }


def _wallet_address(logical: str) -> str | None:
    """논리 지갑명 → 실제 devnet 주소.

    LEDGER_MODE=mock 이어도 주소는 보여준다. wallets/registry.json 은
    setup_wallets.py 가 만든 실제 devnet 키의 공개키 목록이고, mock
    원장은 그 이름을 쓰기만 할 뿐 주소를 새로 만들지 않기 때문이다.
    파일이 없으면(클론 직후) None — 화면은 주소 자리를 비운다.
    """
    try:
        reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return None
    return reg.get(logical)


def _bot_header(bot) -> dict:
    prof = PROFILES.ensure(bot.bot_id, bot.rulebook.label or f"{bot.owner}의 봇")
    return {
        "bot_id": bot.bot_id,
        "owner": bot.owner,
        "name": prof.display_name or bot.rulebook.label or bot.bot_id,
        "model": prof.model,
        "badge": prof.badge,
        "killed": bot.killed,
    }


def _get_bot(bot_id: str, session: str = ""):
    """이 세션이 볼 수 있는 봇만 돌려준다.

    남의 봇이면 403 이 아니라 **404** 다. 403 은 "있긴 한데 권한이 없다"는
    사실을 알려주는 셈이라, 남이 어떤 봇을 만들었는지 셀 수 있게 된다.
    없는 것과 못 보는 것을 구분하지 않는다.
    """
    from app.bots import BOTS
    bot = BOTS.get(bot_id)
    if bot is None or not owns(bot, session):
        raise HTTPException(404, f"봇 없음: {bot_id}")
    return bot


def _my_bots(session: str) -> list:
    """이 세션에 보이는 봇 전부."""
    from app.bots import BOTS
    return [b for b in BOTS.values() if owns(b, session)]


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    """main.require_admin 과 같은 검사.

    main 을 임포트하지 않고 여기서 다시 정의하는 이유는 순환 임포트다
    (main 이 이 라우터를 include 한다). 검사 대상은 같은 config.ADMIN_TOKEN
    하나뿐이라 진실원이 갈라지지는 않는다.

    조회 라우트에는 붙이지 않는다 — 기존 /bots, /bots/{id}/state 와 같은
    수준으로 열어둔다. 붙는 곳은 봇을 만들거나 고치거나 멈추는 곳뿐이다.

    ⚠️ 정의가 이 위치인 이유: Depends(require_admin) 은 라우트를 **선언할
    때** 평가된다. 아래쪽에 두면 그보다 위에 있는 라우트가 NameError 로
    죽는다(실제로 /runtime 을 추가하다 그렇게 됐다).
    """
    if x_admin_token != config.ADMIN_TOKEN:
        raise HTTPException(403, {
            "error": "관리자 인증 실패",
            "hint": "봇을 만들거나 고치는 요청에는 X-Admin-Token 헤더가 필요합니다."})


# ══════ 실행 상태 — 아이폰 목업 위 상태 램프 ═══════════════════════════
#
# [왜 필요한가]
# 자동매매의 값어치는 '사람이 안 보는 동안에도 돈다' 는 것인데, 화면에는
# 그게 도는 중인지 꺼져 있는지가 어디에도 안 나왔다. 스케줄러 상태는
# /scheduler/status 로 볼 수 있었지만 그건 API 이고, 앱을 보는 사람에게는
# 없는 것과 같았다. 켜져 있다는 사실이 안 보이면 자동매매는 시연에서
# 존재하지 않는 기능이 된다.

# 단기 매매 모드의 룰북 값. '활발하게 움직이는 것' 자체가 목적이라
# 익절·손절을 얕게, 보유시간을 짧게, 하루 거래 한도를 크게 잡는다.
#
# ⚠️ 이건 좋은 투자 전략이 아니라 **시연용 설정**이다. 얕은 익절·손절은
#    수수료와 슬리피지를 못 이기는 구간이고, 실제 계좌에 쓰면 잦은 매매가
#    수익을 갉아먹는다. 화면에서 매매가 자주 일어나는 걸 보여주려는 값이다.
SCALP = {
    "take_profit_pct": 0.4,      # 기본 5.0 — 조금만 올라도 판다
    "stop_loss_pct": 0.3,        # 기본 3.0 — 조금만 내려도 끊는다
    # 기본 24시간 → 3분.
    #
    # [실측 2026-08-03] 처음에는 30분(0.5)으로 잡았는데, 100초를 돌려도
    # 청산이 **0건**이었다. 익절·손절은 시세가 움직여야 걸리는데 몇 분
    # 안에 1%씩 움직이는 종목은 없고, 보유 상한 30분은 시연 시간(수 분)
    # 보다 길다. 그래서 '활발한 매매' 를 켰는데 화면에는 매수만 쌓였다.
    # 시연에서 매도를 실제로 보이게 하는 유일한 레버가 보유 상한이다.
    "max_hold_hours": 0.05,      # 3분
    "max_trades_per_day": 40,    # 기본 5
    "min_confidence": 0.55,      # 기본 0.8 — 더 자주 매수까지 간다
}
# 되돌릴 때 쓸 기본값. Rulebook 의 필드 기본값과 같아야 한다.
CALM = {
    "take_profit_pct": 5.0,
    "stop_loss_pct": 3.0,
    "max_hold_hours": 24.0,
    "max_trades_per_day": 5,
    "min_confidence": 0.8,
}
SCALP_INTERVAL = 45              # 단기 모드 주기(초). 기본은 300.


@router.get("/runtime")
async def runtime(session: str = Depends(session_id)):
    """봇이 지금 도는가. 목업 위 상태 램프가 이걸 폴링한다."""
    from app.core.scheduler import SCHEDULER

    mine = _my_bots(session)
    active = [b for b in mine if not b.killed]
    st = SCHEDULER.status()
    # 룰북이 단기 설정이면 그렇게 표시한다. 하나라도 다르면 꺼진 것으로
    # 본다 — '반쯤 켜짐' 을 켜졌다고 하면 화면이 거짓말을 한다.
    scalp = bool(active) and all(
        b.rulebook.max_hold_hours <= SCALP["max_hold_hours"]
        and b.rulebook.take_profit_pct <= SCALP["take_profit_pct"]
        for b in active)
    # 단기 모드는 주기가 짧아야 뜻이 있다. 보유 상한이 3분인데 5분마다
    # 돌면 청산이 항상 늦어서, 화면에는 '3분 지난 포지션' 만 쌓인다.
    if scalp and SCHEDULER.running and SCHEDULER.interval > SCALP_INTERVAL:
        SCHEDULER.interval = SCALP_INTERVAL
    return {
        "running": st["running"] and bool(active),
        "scheduler_running": st["running"],
        "interval_seconds": st["interval_seconds"],
        "next_tick_in": st["next_tick_in"],
        "ticks": st["ticks"],
        "uptime_seconds": st["uptime_seconds"],
        "bots": len(mine),
        "active_bots": len(active),
        "paused_bots": len(mine) - len(active),
        "scalp": scalp,
        # 최근 활동 몇 줄. 램프에 마우스를 올렸을 때 뭘 하고 있었는지.
        "recent": [
            {"who": e["who"], "event": e["event"], "ts": e["ts"]}
            for e in SCHEDULER.log[-5:]
        ],
    }


@router.get("/bots/{bot_id}/progress")
async def bot_progress(bot_id: str, since: int = 0,
                       session: str = Depends(session_id)):
    """진행 중인 사이클의 단계를 나온 것만 골라 돌려준다.

    화면이 [지금 일해보기] 를 누른 뒤 짧은 주기로 이걸 긁어간다. 사이클
    응답은 다 끝나야 오는데 devnet 에서는 30~120초라, 그때까지 화면이
    비어 있으면 '눌렀는데 아무 일도 안 일어난다' 로 보인다.

    since 는 마지막으로 받은 단계 번호다. 그보다 뒤엣것만 오므로 같은
    줄이 두 번 쌓이지 않는다.
    """
    from app.core.progress import PROGRESS
    _get_bot(bot_id, session)          # 남의 봇 진행 상황은 안 보인다
    return PROGRESS.since(bot_id, since)


@router.post("/runtime/scheduler")
async def runtime_scheduler(on: bool = True, interval_seconds: int | None = None,
                            _: None = Depends(require_admin),
                            session: str = Depends(session_id)):
    """상태 램프의 켜기/끄기. /scheduler/start·stop 과 같은 스위치다.

    끌 때 봇의 killed 는 건드리지 않는다. 스케줄러를 멈추는 것과 봇을
    정지시키는 것은 다른 일이고, 섞으면 다시 켰을 때 무엇이 살아나는지
    예측할 수 없게 된다.
    """
    from app.core.scheduler import SCHEDULER

    if not on:
        OWNER_LOG.add("scheduler", session=session, detail="자동매매 정지")
        return {**SCHEDULER.stop(), **(await runtime(session))}

    # 켤 때는 이 세션의 봇 중 정지된 것을 되살린다 — 램프를 켰는데
    # 아무 봇도 안 도는 상황이 제일 헷갈린다.
    woke = []
    for b in _my_bots(session):
        if b.killed:
            b.killed = False
            b.tracker.policy.killed = False
            SCHEDULER.errors.pop(b.bot_id, None)
            woke.append(b.bot_id)
    if woke:
        from app.core import store as STORE_
        STORE_.save()

    SCHEDULER.start(interval_seconds or SCHEDULER.interval)
    OWNER_LOG.add("scheduler", session=session,
                  detail=f"자동매매 시작 (주기 {SCHEDULER.interval}초)"
                         + (f", 봇 {len(woke)}개 재개" if woke else ""))
    return {"woke": woke, **(await runtime(session))}


@router.post("/runtime/scalp")
async def runtime_scalp(on: bool = True,
                        _: None = Depends(require_admin),
                        session: str = Depends(session_id)):
    """단기 매매 모드. 이 세션의 봇 전부에 얕은 익절·손절을 적용한다.

    룰북을 바꾸는 일이라 **무엇이 바뀌었는지 그대로 돌려준다.** 사용자가
    정한 값을 우리가 조용히 갈아치우면 그 뒤의 매매 결과를 해석할 수 없다.
    끄면 Rulebook 기본값으로 되돌린다 — 사용자가 직접 손댄 값이 있었다면
    그것도 같이 덮으므로, 화면에서 그 사실을 밝힌다.
    """
    from app.core import store as STORE_
    from app.core.scheduler import SCHEDULER

    preset = SCALP if on else CALM
    changed = []
    for b in _my_bots(session):
        before = {k: getattr(b.rulebook, k) for k in preset}
        for k, v in preset.items():
            setattr(b.rulebook, k, v)
        changed.append({"bot_id": b.bot_id, "before": before, "after": dict(preset)})
    STORE_.save()

    # 주기도 같이 바꾼다. 30분 보유 상한인데 5분마다 돌면 청산이 늦다.
    if SCHEDULER.running:
        SCHEDULER.interval = SCALP_INTERVAL if on else 300
    OWNER_LOG.add("scheduler", session=session,
                  detail=("단기 매매 모드 켬" if on else "단기 매매 모드 끔")
                         + f" (봇 {len(changed)}개)")
    return {"scalp": on, "changed": changed,
            "interval_seconds": SCHEDULER.interval,
            **(await runtime(session))}




# ══════ 홈 ═════════════════════════════════════════════════════════
@router.get("/overview")
async def overview(session: str = Depends(session_id)):
    """홈 화면 한 번에. 총자산 + 봇 카드 전부.

    **이 브라우저가 만든 봇만** 보인다(app/core/session.py).
    """
    from app.adapters import gemini_byok, kis_quotes

    rate_info = await FX.usd_krw()
    rate = rate_info["rate"]

    # 시세는 전 봇을 통틀어 한 번만 받는다.
    prices = await _prices({p.ticker for p in BOOK.all()})

    cards = []
    total_micro = 0
    wallets = []
    for bot in _my_bots(session):
        val = await _bot_valuation(bot, prices)
        ret = _return_pct(bot.bot_id, val)
        total_micro += val["total_micro"]

        addr = _wallet_address(bot.w("user-treasury"))
        wallets.append({"bot_id": bot.bot_id, "label": _bot_header(bot)["name"],
                        "logical": bot.w("user-treasury"), "address": addr,
                        "total_micro": val["total_micro"],
                        "total_krw": FX.to_krw(val["total_micro"], rate)})

        cards.append({
            **_bot_header(bot),
            "value_micro": val["total_micro"],
            "value_krw": FX.to_krw(val["total_micro"], rate),
            "return_pct": ret["pct"],
            "open_positions": len(val["positions"]),
            "self_funding": val["per_wallet"]["revenue-wallet"] > 0,
        })

    return {
        "wallet": {
            "total_micro": total_micro,
            "total_krw": FX.to_krw(total_micro, rate),
            "total_usd": round(total_micro / USDC, 2),
            "accounts": wallets,
        },
        "fx": rate_info,
        "bots": cards,
        # 화면 하단의 상태 배지용. 셋 다 독립 축이라 따로 내보낸다
        # (README '모드 스위치' 절과 같은 구분).
        "ledger_mode": _LEDGER_MODE,
        "inference_mode": config.INFERENCE_MODE,
        # 선언(mode)과 가용성(키가 실제로 있는가)은 다른 축이다.
        # byok 라고 적어놓고 키가 없으면 전 판단이 조용히 모의로 돈다.
        "inference_live": gemini_byok.enabled(),
        # 기본값은 external.spot() 과 같아야 한다. 예전에는 여기만 "mock"
        # 이라 실시세로 체결되는데 화면은 "시세 mock" 이라고 말했다.
        "price_source": os.environ.get("PRICE_SOURCE", "kis").lower(),
        "price_live": (os.environ.get("PRICE_SOURCE", "kis").lower() == "kis"
                       and kis_quotes.enabled()),
    }


# ══════ 봇 상세 · 요약 탭 ══════════════════════════════════════════
def _latest_thesis(bot_id: str):
    """이 봇이 가장 최근에 만든 투자 판단. AI 요약 리포트의 원문이다."""
    from app.main import THESES
    mine = [t for t in THESES.values() if t.bot_id == bot_id]
    return max(mine, key=lambda t: t.ts) if mine else None


def _report(bot_id: str, val: dict) -> dict:
    """AI 요약 리포트.

    지어내지 않는다. 재료는 셋 다 실제 기록이다.
      · 가장 최근 테제의 근거 문장 (Gemini 또는 모의 판단의 출력 원문)
      · 그 결정의 영수증이 밝히는 추론 출처
      · 저널에 남은 최근 체결
    아무것도 없으면 '아직 판단한 것이 없다'고 말한다. 그것도 사실이다.
    """
    th = _latest_thesis(bot_id)
    fills = JOURNAL.fills_of(bot_id)[-5:]

    if th is None:
        return {
            "text": "아직 이 봇이 내린 투자 판단이 없습니다. "
                    "사이클을 한 번 돌리면 뉴스 구매 → 스크리닝 → 심층 추론을 "
                    "거쳐 첫 판단이 만들어집니다.",
            "generated_at": None, "empty": True,
            "inference_mode": None, "degraded": False, "sources": {},
            "highlights": [],
        }

    receipt = RECEIPTS.receipts.get(th.receipt_id)
    from app.adapters import universe
    company = universe.company_name(th.ticker)

    side_ko = {"buy": "매수", "sell": "매도"}.get(th.side or "", "관망")
    parts = [
        f"{company}({th.ticker})에 대해 확신도 {th.confidence:.2f}로 "
        f"{side_ko} 판단했습니다.",
        th.rationale.strip(),
    ]
    if fills:
        buys = sum(1 for f in fills if f["side"] == "buy")
        sells = len(fills) - buys
        parts.append(f"최근 체결은 매수 {buys}건 · 매도 {sells}건입니다.")
    if val["positions"]:
        parts.append(
            f"현재 {len(val['positions'])}개 포지션을 들고 있으며 "
            f"평가손익은 {val['unrealized_micro'] / USDC:+.2f} USDC입니다.")

    # 화면에서 강조 색이 붙는 조각들. 프론트가 문자열을 다시 파싱하지
    # 않도록 여기서 무엇을 강조할지까지 정해 보낸다.
    highlights = [
        {"text": side_ko, "tone": "down" if th.side == "sell" else "up"},
        {"text": f"확신도 {th.confidence:.2f}", "tone": "brand"},
    ]
    if val["unrealized_micro"]:
        highlights.append({
            "text": f"{val['unrealized_micro'] / USDC:+.2f} USDC",
            "tone": "up" if val["unrealized_micro"] > 0 else "down"})

    return {
        "text": " ".join(p for p in parts if p),
        "generated_at": th.ts,
        "empty": False,
        "ticker": th.ticker,
        "company": company,
        "flag": MARKETS_.flag(th.ticker),
        "side": th.side,
        "confidence": th.confidence,
        "inference_mode": receipt.inference_mode if receipt else None,
        "degraded": bool(receipt and receipt.degraded),
        "sources": dict(receipt.inference_sources) if receipt else {},
        "highlights": highlights,
    }


RANGES = {
    "1w": 7 * 86_400,
    "1m": 30 * 86_400,
    "3m": 90 * 86_400,
    "6m": 180 * 86_400,
    "all": None,
}


def _equity_series(bot_id: str, key: str) -> dict:
    """수익률 곡선.

    ⚠️ 여기서 데이터를 만들어내지 않는다. 서버가 실제로 관측한 시점만
    점으로 찍힌다. 방금 켰다면 점이 몇 개뿐이고, 그러면 화면도 그렇게
    보여야 한다. 3개월 곡선은 3개월을 돌려야 생긴다.
    """
    window = RANGES.get(key, RANGES["3m"])
    since = None if window is None else time.time() - window
    pts = JOURNAL.equity_of(bot_id, since)

    first = pts[0]["total"] if pts else 0
    last = pts[-1]["total"] if pts else 0
    pct = round((last - first) / first * 100, 2) if first > 0 and len(pts) >= 2 else None

    span = (pts[-1]["ts"] - pts[0]["ts"]) if len(pts) >= 2 else 0.0
    return {
        "range": key,
        "points": [{"ts": p["ts"], "total_micro": p["total"]} for p in pts],
        "window_return_pct": pct,
        "span_seconds": round(span),
        # 요청한 구간을 데이터가 실제로 덮는가. 화면이 "누적 중"을
        # 표시할지 판단하는 근거다.
        "covers_range": bool(window and span >= window * 0.9),
    }


@router.get("/bots/{bot_id}/summary")
async def bot_summary(bot_id: str, range: str = "3m",
                      session: str = Depends(session_id)):
    bot = _get_bot(bot_id, session)
    rate_info = await FX.usd_krw()
    rate = rate_info["rate"]

    val = await _bot_valuation(bot)
    ret = _return_pct(bot_id, val)

    # 열린 포지션을 종목별로 묶어 보유 비중을 만든다.
    from app.adapters import universe
    by_ticker: dict[str, dict] = {}
    for p in val["positions"]:
        price = val["prices"].get(p.ticker, p.entry_price)
        v = p.qty * price // USDC
        row = by_ticker.setdefault(p.ticker, {
            "ticker": p.ticker, "company": universe.company_name(p.ticker),
            # 어느 나라 주식인지. 환전 없이 전 세계를 산다는 것이 이
            # 제품의 주장이라, 종목 코드만으로는 그게 안 읽힌다.
            "flag": MARKETS_.flag(p.ticker),
            "value_micro": 0, "basis_micro": 0, "qty": 0})
        row["value_micro"] += v
        row["basis_micro"] += p.basis
        row["qty"] += p.qty

    holdings = sorted(by_ticker.values(), key=lambda r: -r["value_micro"])[:5]
    market_total = sum(r["value_micro"] for r in holdings)
    for i, r in enumerate(holdings):
        r["weight_pct"] = round(r["value_micro"] / market_total * 100, 1) if market_total else 0.0
        r["value_krw"] = FX.to_krw(r["value_micro"], rate)
        r["qty_display"] = round(r["qty"] / USDC, 6)
        r["color"] = SERIES_COLORS[i % len(SERIES_COLORS)]

    # 자산 곡선에 지금 시점을 한 점 더 찍는다. 화면을 열 때마다 최신
    # 값이 곡선 끝에 붙어야 "현재 자산"과 곡선의 끝이 어긋나지 않는다.
    JOURNAL.record_equity(bot_id, val["total_micro"], val["cash_micro"],
                          val["market_micro"])

    return {
        **_bot_header(bot),
        "report": _report(bot_id, val),
        "equity": _equity_series(bot_id, range),
        "ranges": list(RANGES),
        "performance": {
            "current_micro": val["total_micro"],
            "current_krw": FX.to_krw(val["total_micro"], rate),
            "basis_micro": val["basis_micro"],
            "basis_krw": FX.to_krw(val["basis_micro"], rate),
            "unrealized_micro": val["unrealized_micro"],
            "unrealized_krw": FX.to_krw(val["unrealized_micro"], rate),
            "unrealized_pct": (round(val["unrealized_micro"] / val["basis_micro"] * 100, 2)
                               if val["basis_micro"] else None),
            "total_return_pct": ret["pct"],
            "realized_micro": ret["realized_micro"],
            "cash_micro": val["cash_micro"],
            "market_micro": val["market_micro"],
        },
        "holdings": holdings,
        "wallets": {r: val["per_wallet"][r] for r in WALLET_ROLES},
        "fx": rate_info,
    }


# ══════ 봇 상세 · 거래 내역 탭 ═════════════════════════════════════
@router.get("/bots/{bot_id}/trades")
async def bot_trades(bot_id: str, limit: int = 50,
                     session: str = Depends(session_id)):
    bot = _get_bot(bot_id, session)
    rate_info = await FX.usd_krw()
    rate = rate_info["rate"]
    from app.adapters import universe

    fills = JOURNAL.fills_of(bot_id)
    receipts = [r for r in RECEIPTS.receipts.values() if r.bot_id == bot_id]

    # 체결 성공률 = 실제로 체결된 결정 ÷ 내려진 결정 전부.
    # 룰북에 막히거나 자본 청구가 거절돼 못 산 결정이 분모에 남는다.
    decided = len(receipts)
    executed = sum(1 for r in receipts if r.execution_tx)

    # 승률 = 정산된 결정 중 이익이 난 비율. RECEIPTS.stats()는 표본이
    # 적으면 보수적 기본값(0.5)을 돌려주는데, 그건 MVoT 계산용 값이지
    # 사용자에게 보여줄 성적이 아니다. 여기서는 날것을 센다.
    settled = [r for r in receipts if r.settled_at is not None]
    wins = sum(1 for r in settled if (r.realized_pnl or 0) > 0)

    # 평균 보유기간 — 매도 체결에 기록된 실제 보유 시간의 평균.
    holds = [f["hold_hours"] for f in fills
             if f["side"] == "sell" and f.get("hold_hours") is not None]

    rows = []
    for f in reversed(fills[-limit:]):
        rows.append({
            "fill_id": f["fill_id"],
            "ts": f["ts"],
            "ticker": f["ticker"],
            "company": universe.company_name(f["ticker"]),
            "flag": MARKETS_.flag(f["ticker"]),
            "side": f["side"],
            "side_ko": "매수" if f["side"] == "buy" else "매도",
            "qty": round(f["qty"] / USDC, 6),
            "price_micro": f["price_micro"],
            "price_krw": FX.to_krw(f["price_micro"], rate),
            "gross_micro": f["gross_micro"],
            "gross_krw": FX.to_krw(f["gross_micro"], rate),
            "pnl_micro": f["pnl_micro"],
            "reason": f["reason"],
            "tx": f["tx"],
            # 이 체결이 실제로 온체인에 있다는 증거. devnet 서명일 때만 링크가
            # 붙는다 — 화면의 수량과 체인의 수량이 같은지 직접 볼 수 있어야 한다.
            "explorer": _explorer(f["tx"]),
        })

    return {
        **_bot_header(bot),
        "ledger_mode": _LEDGER_MODE,
        "summary": {
            "total_fills": len(fills),
            "buys": sum(1 for f in fills if f["side"] == "buy"),
            "sells": sum(1 for f in fills if f["side"] == "sell"),
            "avg_hold_hours": round(sum(holds) / len(holds), 1) if holds else None,
            "fill_rate_pct": round(executed / decided * 100, 1) if decided else None,
            "win_rate_pct": round(wins / len(settled) * 100, 1) if settled else None,
            "decisions": decided,
            "settled": len(settled),
            "open_positions": len(BOOK.of_bot(bot_id)),
        },
        "rows": rows,
        "fx": rate_info,
    }


# ══════ 봇 상세 · API 탭 ═══════════════════════════════════════════
# 이 봇이 앞으로 더 살 수 있는 것들.
#
# 두 종류가 섞여 있고, `ready` 가 그 둘을 가른다.
#   ready=True   이 리포지토리에 어댑터가 있다. 환경변수 하나로 켜진다.
#   ready=False  아직 없다 — 구상이거나 미확정이다.
# 화면은 이 값을 그대로 '연결하기' 와 '준비 중' 으로 보여준다.
# 붙지도 않은 것을 붙은 것처럼 두면 이 목록 전체를 믿을 수 없게 된다.
RECOMMENDED = [
    # ── 아직 붙지 않은 것들을 맨 앞에 둔다 ──────────────────────────
    # 이 화면이 답해야 하는 질문은 "이 봇이 앞으로 무엇을 더 살 수 있나"
    # 이고, 그 답으로 가장 하고 싶은 이야기가 국내 데이터 제휴다.
    #
    # ⚠️ 둘 다 **구상 단계다.** 어댑터도 계약도 없다. 그래서 ready=False 로
    #    두고 화면이 '준비 중' 이라고 말한다 — 붙지도 않은 것을 붙은 것처럼
    #    보이게 하면 이 목록 전체를 믿을 수 없게 된다.
    {"key": "shinhan", "name": "신한은행 리포트",
     "tags": ["리서치", "국내"], "price_note": "제휴 필요",
     "desc": "은행 리서치센터의 종목·업종 리포트를 봇이 호출당 결제로 사서 "
             "심층 추론의 근거로 씁니다. 구상 단계입니다.",
     "adapter": "(미구현)", "ready": False},
    {"key": "toss", "name": "토스 종토방",
     "tags": ["여론", "커뮤니티"], "price_note": "제휴 필요",
     "desc": "종목토론방의 여론 흐름을 지표로 받아 뉴스와 함께 봅니다. "
             "구상 단계입니다.",
     "adapter": "(미구현)", "ready": False},
    {"key": "gemini-gateway", "name": "Gemini 게이트웨이 (mainnet)",
     "tags": ["AI 모델", "x402"], "price_note": "mainnet 실결제",
     "desc": "INFERENCE_MODE=managed 로 켜면 사용자 키 대신 게이트웨이에 "
             "mainnet 결제로 추론을 삽니다.",
     "adapter": "app/adapters/gemini_live.py", "ready": True},
    {"key": "smartmoney-universe", "name": "smartmoney.market 유니버스",
     "tags": ["종목", "SEC"], "price_note": "무료",
     "desc": "SEC Form 4 내부자 추적 1,045종목. 룰북 종목 검증에 씁니다.",
     "adapter": "app/adapters/universe.py", "ready": True},
    {"key": "paysh-quotes", "name": "pay.sh MPP 실시간 시세",
     "tags": ["시세", "x402"], "price_note": "localnet 결제",
     "desc": "PRICE_SOURCE=live 로 켜면 내장 기준가 대신 실시세를 씁니다.",
     "adapter": "app/adapters/live_quotes.py", "ready": True},
    {"key": "paysh-gateway", "name": "pay.sh 결제 게이트웨이",
     "tags": ["결제", "x402"], "price_note": "미정",
     "desc": "X402_MODE=paysh. 라우트 주소·메서드·챌린지 위치가 아직 미확정입니다.",
     "adapter": "app/inference.py", "ready": False},
]


@router.get("/bots/{bot_id}/apis")
async def bot_apis(bot_id: str, session: str = Depends(session_id)):
    bot = _get_bot(bot_id, session)
    rate_info = await FX.usd_krw()
    rate = rate_info["rate"]

    calls = JOURNAL.api_calls_of(bot_id)
    total_calls = len(calls)
    total_spend = sum(c["amount"] for c in calls)

    # 일 평균 — 관측 기간으로 나눈다. 봇을 만든 지 1시간이면 분모가
    # 1일이 아니라 1/24일이어야 "일 평균"이 뻥튀기되지 않는다.
    # 다만 하루 미만은 하루로 본다 (분모가 0에 가까우면 값이 폭발한다).
    span_days = 1.0
    if calls:
        span_days = max(1.0, (calls[-1]["ts"] - calls[0]["ts"]) / 86_400)

    grouped: dict[str, dict] = {}
    for c in calls:
        info = classify(c["resource"])
        g = grouped.setdefault(info["key"], {
            "key": info["key"], "name": info["name"], "tags": info["tags"],
            "paysh": info["paysh"], "kind": info["kind"],
            "calls": 0, "spend_micro": 0})
        g["calls"] += 1
        g["spend_micro"] += c["amount"]

    connected = sorted(grouped.values(), key=lambda g: -g["spend_micro"])
    for g in connected:
        g["calls_pct"] = round(g["calls"] / total_calls * 100, 1) if total_calls else 0.0
        g["spend_pct"] = round(g["spend_micro"] / total_spend * 100, 1) if total_spend else 0.0
        g["spend_krw"] = FX.to_krw(g["spend_micro"], rate)

    # 최근 호출을 건별로 내보낸다. 합계만 보여주면 "호출당 결제"라는 주장을
    # 확인할 방법이 없다 — 심사위원이 서명 하나를 짚어 Explorer 에서 직접
    # 대조할 수 있어야 한다.
    recent = []
    for c in reversed(calls[-30:]):
        info = classify(c["resource"])
        recent.append({
            "ts": c["ts"],
            "name": info["name"],
            "key": info["key"],
            "resource": c["resource"],
            "amount_micro": c["amount"],
            "amount_krw": FX.to_krw(c["amount"], rate),
            # 그 호출에 실제로 답한 모델 ID. 모의 판단이면 "mock" 이 온다.
            "model": c.get("model", ""),
            "tx": c.get("tx", ""),
            "explorer": _explorer(c.get("tx", "")),
        })

    return {
        **_bot_header(bot),
        "recent": recent,
        "ledger_mode": _LEDGER_MODE,
        "summary": {
            "calls": total_calls,
            "spend_micro": total_spend,
            "spend_krw": FX.to_krw(total_spend, rate),
            "calls_per_day": round(total_calls / span_days, 1),
            "spend_per_day_micro": int(total_spend / span_days),
            "spend_per_day_krw": FX.to_krw(int(total_spend / span_days), rate),
            "span_days": round(span_days, 2),
        },
        "connected": connected,
        "recommended": [r for r in RECOMMENDED
                        if r["key"] not in grouped],
        "price_table": {
            "exa_search": config.PRICE_EXA_SEARCH,
            "gemini_flash": config.PRICE_GEMINI_FLASH,
            "gemini_deep": config.PRICE_GEMINI_DEEP,
            "market_quote": config.PRICE_MARKET_DATA,
        },
        "known_providers": list(PROVIDERS.values()),
        "fx": rate_info,
    }


# ══════ 봇 상세 · 대화 ═════════════════════════════════════════════
class ChatTurn(BaseModel):
    role: str = Field(..., pattern="^(user|model)$")
    text: str = Field(..., max_length=2000)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    # 앞선 대화. 없으면 매 질문이 첫 질문이 되어 봇이 방금 한 말도 잊는다.
    # 서버가 대화를 저장하지 않는 이유는 봇 하나에 사람 하나가 아니고,
    # 어차피 화면이 그 목록을 이미 들고 있기 때문이다.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


SUGGESTIONS = [
    "매매 내역을 정리해줘",
    "시장 흐름은 어때?",
    "지금 달러는 어때?",
    "왜 그 종목을 샀어?",
    "지금 손익이 어떻게 돼?",
]

# 모델을 못 부를 때(키 없음·429·시간 초과) 주제를 가르는 최소한의 산수.
# 모델이 답할 수 있으면 판정도 모델이 한다 — 이건 폴백 경로 전용이다.
# 넓게 잡는다: 애매하면 통과시키고, 명백히 다른 주제만 거절한다.
ON_TOPIC_WORDS = (
    "주식", "종목", "매수", "매도", "매매", "거래", "투자", "수익", "손익",
    "손절", "익절", "포지션", "보유", "자산", "잔고", "시장", "시세", "차트",
    "환율", "달러", "원화", "usd", "krw", "usdc", "룰북", "규칙", "설정",
    "봇", "판단", "근거", "이유", "왜", "확신도", "api", "수수료", "지갑",
    "리스크", "위험", "배당", "실적", "뉴스", "반도체", "빅테크",
)


async def _answer_from_state(bot, message: str) -> str:
    """봇의 실제 상태로 답한다. Gemini가 꺼져 있어도 거짓말은 안 한다.

    LLM이 없을 때 "죄송합니다"만 돌려주는 대신, 사용자가 물어본 것에
    대해 서버가 이미 아는 사실을 정리해 준다. 이 답은 전부 원장·저널에서
    나오므로 틀릴 수가 없다.
    """
    from app.adapters import universe

    val = await _bot_valuation(bot)
    ret = _return_pct(bot.bot_id, val)
    rate_info = await FX.usd_krw()
    q = message.lower()

    def money(micro: int) -> str:
        return f"{FX.to_krw(micro, rate_info['rate']):,}원 ({micro / USDC:,.2f} USDC)"

    if any(k in message for k in ("달러", "환율", "USD", "원화")):
        return (f"현재 환율은 1 USD = {rate_info['rate']:,.2f}원입니다 "
                f"(출처 {rate_info['source']}). "
                f"이 봇의 총 자산 {val['total_micro'] / USDC:,.2f} USDC는 "
                f"{FX.to_krw(val['total_micro'], rate_info['rate']):,}원입니다.")

    if any(k in message for k in ("매매", "거래", "내역", "체결")):
        fills = JOURNAL.fills_of(bot.bot_id)
        if not fills:
            return "아직 체결된 거래가 없습니다. 사이클을 돌리면 첫 매수가 잡힙니다."
        buys = [f for f in fills if f["side"] == "buy"]
        sells = [f for f in fills if f["side"] == "sell"]
        last = fills[-1]
        return (f"총 {len(fills)}건 체결했습니다 — 매수 {len(buys)}건, 매도 {len(sells)}건. "
                f"가장 최근은 {universe.company_name(last['ticker'])} "
                f"{'매수' if last['side'] == 'buy' else '매도'} "
                f"{last['qty'] / USDC:.6f}주 @ {last['price_micro'] / USDC:,.2f} USDC입니다. "
                f"실현손익은 {money(ret['realized_micro'])}입니다.")

    if any(k in message for k in ("손익", "수익", "얼마", "성적", "잔고", "자산")):
        pct = f"{ret['pct']:+.2f}%" if ret["pct"] is not None else "아직 없음"
        return (f"총 자산은 {money(val['total_micro'])}입니다. "
                f"현금 {money(val['cash_micro'])}, 평가액 {money(val['market_micro'])}. "
                f"투입원가 대비 총 수익률은 {pct}이고, 평가손익은 "
                f"{money(val['unrealized_micro'])}입니다.")

    if any(k in message for k in ("왜", "이유", "근거", "판단", "샀")):
        rep = _report(bot.bot_id, val)
        return rep["text"]

    if any(k in message for k in ("시장", "흐름", "종목", "보유")):
        if not val["positions"]:
            return (f"현재 열린 포지션이 없습니다. 룰북 허용 종목은 "
                    f"{', '.join(sorted(bot.rulebook.allowed_tickers))}입니다.")
        lines = []
        for p in val["positions"]:
            price = val["prices"].get(p.ticker, p.entry_price)
            lines.append(f"{universe.company_name(p.ticker)} "
                         f"{p.pnl_pct(price):+.2f}% ({p.held_hours():.1f}시간 보유)")
        return "보유 중인 포지션입니다 — " + " / ".join(lines) + "."

    if any(k in message for k in ("룰", "규칙", "설정", "손절", "익절")):
        rb = bot.rulebook
        return (f"제 룰북입니다. 허용 종목 {', '.join(sorted(rb.allowed_tickers))}, "
                f"확신도 하한 {rb.min_confidence}, 1회 최대 투입 "
                f"{rb.max_position_usdc / USDC:,.0f} USDC, "
                f"익절 +{rb.take_profit_pct}% / 손절 -{rb.stop_loss_pct}% / "
                f"최대 보유 {rb.max_hold_hours}시간입니다. "
                f"제가 무슨 판단을 하든 이 규칙이 최종 거부권을 갖습니다.")

    pct = f"{ret['pct']:+.2f}%" if ret["pct"] is not None else "집계 전"
    return (f"총 자산 {money(val['total_micro'])}, 총 수익률 {pct}, "
            f"열린 포지션 {len(val['positions'])}개입니다. "
            f"매매 내역·보유 종목·룰북·환율 중 무엇이 궁금하신지 알려주세요.")


# 매매 지시로 볼 말. 모델이 action 을 만들어도 사용자가 실제로 이런 말을
# 하지 않았으면 집행하지 않는다 — 열쇠 두 개를 요구하는 셈이다.
# 모델이 지시로 착각해 돈을 움직이는 것이 이 기능의 유일한 위험이라
# 서버가 사용자의 원문을 한 번 더 본다.
BUY_WORDS = ("사줘", "사 줘", "사자", "사라", "매수해", "매수 해", "매수하",
             "구매해", "구매하", "들어가자", "담아", "매집", "사놔", "사둬")
SELL_WORDS = ("팔아", "팔자", "팔라", "팔아줘", "매도해", "매도 해", "매도하",
              "청산해", "청산하", "정리해", "익절해", "손절해", "빼줘", "팔아라")
ORDER_WORDS = BUY_WORDS + SELL_WORDS

# 반대로 '의견을 묻는 말'. 여기 걸리면 지시로 보지 않는다.
ASK_WORDS = ("살까", "팔까", "사도 될까", "어떨까", "괜찮을까", "어때",
             "추천", "의견", "생각해", "봐줘", "분석")

_AMOUNT_RE = re.compile(
    r"(\d[\d,]*(?:\.\d+)?)\s*(달러|불|\$|usd|usdc|만원|원)?", re.IGNORECASE)


def _parse_order(message: str, bot) -> dict | None:
    """사용자의 말에서 주문을 읽는다. 지시가 아니면 None.

    [왜 모델이 아니라 서버가 읽나]
    처음에는 모델이 낸 action 만 집행했는데, 모델이 "제 투자 원칙상
    지금은 매수하지 않겠습니다" 라며 지시를 거절했다(실측). 그건 모델이
    판단할 일이 아니다 — **주문은 소유자의 권한**이고, 봇의 원칙은
    자기가 알아서 판단할 때 적용되는 것이다.

    그래서 지시 해석은 서버가 한다. 모델의 action 은 금액·종목을 보태는
    보조 재료로만 쓴다.
    """
    orders = _parse_orders(message, bot)
    return orders[0] if orders else None


# "총 300달러" · "합쳐서" · "나눠서" — 금액을 종목 수로 쪼개라는 뜻.
SPLIT_WORDS = ("총", "합쳐", "합해", "나눠", "나누어", "분산", "골고루", "균등")


def _parse_orders(message: str, bot) -> list[dict]:
    """사용자의 말에서 주문을 **전부** 읽는다. 지시가 아니면 빈 목록.

    [여러 종목 — 2026-08-03]
    예전에는 `find_tickers` 가 목록을 돌려주는데도 `found[0]` 만 썼다.
    그래서 "엔비디아 테슬라 애플 사줘" 는 엔비디아 하나만 사고 끝났다.
    사용자는 셋 다 샀다고 생각하는데 실제로는 하나였다 — 조용히 덜
    실행되는 것이 실패보다 나쁘다. 이제 찾은 종목마다 주문을 만든다.

    [금액을 어떻게 나누나]
    "각 100달러씩" 과 "총 300달러" 는 다른 말이다. 그런데 대부분의 사람은
    금액 하나만 말한다. 기본은 **종목당** 으로 읽고, 총액·합쳐서·나눠서
    같은 말이 있을 때만 종목 수로 쪼갠다. 어느 쪽으로 읽었는지는 집행
    결과 문장에 그대로 적어서, 사용자가 오해한 채로 넘어가지 않게 한다.
    """
    said_buy = any(w in message for w in BUY_WORDS)
    said_sell = any(w in message for w in SELL_WORDS)
    if not (said_buy or said_sell):
        return []
    # "살까?" 처럼 묻기만 하는 말이면 지시가 아니다.
    if any(w in message for w in ASK_WORDS) and not (said_buy or said_sell):
        return []

    side = "sell" if said_sell else "buy"
    found = MARKETS_.find_tickers(message)
    amount = _parse_amount(message)

    if not found:
        # 종목을 못 찾았어도 주문 의사는 분명하다. 한 건으로 올려보내고
        # 종목 판별은 _execute_one 이 모델 action 까지 봐서 마저 한다.
        return [{"side": side, "ticker": "", "amount_usd": amount,
                 "of": 1, "index": 1}]

    # 금액을 쪼갤지 결정한다. 종목이 하나뿐이면 쪼갤 것도 없다.
    split = len(found) > 1 and any(w in message for w in SPLIT_WORDS)
    each = (amount / len(found)) if split else amount
    return [{"side": side, "ticker": t, "amount_usd": each,
             "of": len(found), "index": i + 1, "split": split}
            for i, t in enumerate(found)]


def _parse_amount(message: str) -> float:
    """"30달러어치" · "$50" · "10만원" → USD. 못 찾으면 0."""
    for num, unit in _AMOUNT_RE.findall(message):
        try:
            value = float(num.replace(",", ""))
        except ValueError:
            continue
        u = (unit or "").lower()
        if u in ("달러", "불", "$", "usd", "usdc"):
            return value
        if u == "만원":
            return value * 10_000 / 1_400      # 대략 환산. 정확한 값은 아니다
        if u == "원":
            return value / 1_400
    return 0.0


async def _execute_order(bot, action: dict, message: str):
    """대화로 받은 매매 지시를 집행한다. (결과, 사용자에게 덧붙일 문장)

    [룰북보다 먼저 집행하는 이유]
    룰북은 에이전트의 자율 판단에 걸리는 규칙이지, 소유자의 직접 주문까지
    막으라는 뜻이 아니다 — main.manual_buy 주석 참조. 대신 영수증에
    manual 로 찍혀 에이전트의 적중률에서는 빠진다.

    [두 열쇠]
    모델이 action 을 냈다는 것만으로는 집행하지 않는다. 사용자의 원문에도
    지시하는 말이 있어야 한다. 모델이 "살까?" 를 매수 지시로 읽는 순간
    돈이 움직이면, 그건 사용자가 의도한 적 없는 체결이다.
    """
    parsed = _parse_order(message, bot)
    if parsed is None:
        return None, ("(주문으로 실행하려면 '사줘' · '팔아' 처럼 분명히 "
                      "말씀해 주세요. 지금은 의견만 드렸습니다.)")
    return await _execute_one(bot, parsed, action, message)


async def _execute_one(bot, parsed: dict, action: dict | None, message: str):
    """주문 **한 건**을 집행한다. (결과, 사용자에게 덧붙일 문장)

    parsed 는 _parse_orders 가 읽어둔 한 건이다. 여러 종목 주문은
    이 함수를 종목 수만큼 부른다 — 한 건이 실패해도 나머지는 계속
    간다. 세 종목 중 하나가 잔고 부족이라고 나머지 둘까지 취소하면,
    사용자는 왜 아무것도 안 샀는지 알 수 없다.
    """
    from app.main import manual_buy, manual_sell

    # 서버가 읽은 지시가 우선이다. 모델의 action 은 종목·금액을 보탤 때만 쓴다.
    action = {
        "side": parsed["side"],
        "ticker": parsed["ticker"] or (action or {}).get("ticker", ""),
        "amount_usd": parsed["amount_usd"] or (action or {}).get("amount_usd", 0),
    }
    # 여러 건 중 몇 번째인지. 한 건짜리 주문에는 안 붙는다.
    tag = (f"[{parsed['index']}/{parsed['of']}] "
           if parsed.get("of", 1) > 1 else "")

    raw = (action.get("ticker") or "").strip()
    # 모델이 회사 이름으로 답할 수도 있다. 우리 카탈로그에서 되찾는다.
    found = MARKETS_.find_tickers(raw) or MARKETS_.find_tickers(message)
    base = found[0] if found else (raw[:-1] if raw.endswith("x") else raw)
    if not base:
        return None, f"{tag}(어느 종목인지 알 수 없어 실행하지 않았습니다.)"

    allowed = {t[:-1] if t.endswith("x") else t
               for t in bot.rulebook.allowed_tickers}
    ticker = f"{base}x"

    try:
        if action["side"] == "sell":
            r = await manual_sell(bot, ticker, message)
            pnl = sum((x.get("realized_pnl") or 0) for x in r["results"]
                      if isinstance(x, dict))
            return r, (f"{tag}✅ 지시대로 {MARKETS_.company(base)} 전량 매도했습니다 — "
                       f"{r['closed']}건 청산, 실현손익 {pnl / USDC:+,.2f} USDC.")

        # 매수 — 금액을 안 말했으면 1회 최대 투입의 절반으로 잡는다.
        amount = float(action.get("amount_usd") or 0)
        size = int(amount * USDC) if amount > 0 else bot.rulebook.max_position_usdc // 2
        size = min(size, bot.rulebook.max_position_usdc)
        r = await manual_buy(bot, ticker, size, message)
        extra = ("" if base in allowed else
                 f" (참고: {MARKETS_.company(base)}는 이 봇의 룰북 목록 밖입니다 — "
                 f"직접 지시하셔서 실행했습니다.)")
        # 금액을 종목 수로 쪼갰으면 그렇게 읽었다고 밝힌다. 사용자가
        # "총 300" 이라고 했는지 "각 300" 이라고 했는지는 우리 해석이고,
        # 해석을 감추면 900달러가 나간 뒤에야 알게 된다.
        if parsed.get("split"):
            extra += f" (총액을 {parsed['of']}종목으로 나눠 집행했습니다.)"
        return r, (f"{tag}✅ 지시대로 {MARKETS_.company(base)} "
                   f"{r['qty']:.6f}주를 ${r['price_usd']:,.2f}에 매수했습니다"
                   f" (총 ${r['size_usd']:,.2f}).{extra}")
    except HTTPException as e:
        d = e.detail if isinstance(e.detail, dict) else {"error": str(e.detail)}
        return None, (f"{tag}⚠️ {MARKETS_.company(base)} 실행하지 못했습니다 — "
                      f"{d.get('error')} {d.get('hint', '')}").strip()
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 수동 주문 실패: {type(e).__name__}: {str(e)[:120]}")
        return None, f"{tag}⚠️ 실행 중 오류가 났습니다 ({type(e).__name__})."


async def _maybe_execute_chat_order(bot, action: dict | None, message: str):
    """대화에 주문이 들어 있으면 집행하고 **구조화된** 결과를 함께 돌려준다.

    돌려주는 것: (trade, order, note)

    [왜 order 를 따로 만드나 — 2026-08-03]
    예전에는 집행 결과가 `note` 문자열로만 나왔고, 그건 답변 문장 뒤에
    이어붙여져 대화창에만 남았다. 그래서 오른쪽 **실행 로그에는 아무것도
    안 떴다** — 사용자는 "사줘" 라고 했고 봇은 실제로 샀는데, 화면의
    기록에는 그 사건이 존재하지 않았다. [지금 일해보기] 로 산 것만 로그에
    보이니 "대화로 시킨 건 안 먹혔나?" 로 읽힌다.

    order 는 집행 여부(executed)와 사유(note)를 구조로 들고 있어서
    화면이 그대로 한 줄 찍을 수 있다. 실패한 주문도 로그에 남는다 —
    안 된 이유가 안 보이는 것이 안 된 것보다 나쁘다.
    """
    orders = _parse_orders(message, bot)
    if not (action or orders):
        return None, None, None          # 주문이 아니다. 그냥 대화.

    if not orders:
        # 모델만 action 을 냈다. 종목을 모르는 한 건으로 취급한다.
        orders = [{"side": (action or {}).get("side", "buy"), "ticker": "",
                   "amount_usd": 0, "of": 1, "index": 1}]

    # 종목마다 따로 집행한다. **한 건이 실패해도 멈추지 않는다** — 세 종목
    # 중 하나가 잔고 부족이라고 나머지를 취소하면, 사용자는 왜 아무것도
    # 안 샀는지 알 수 없다. 각각의 성패를 그대로 모아 보고한다.
    trades, notes, legs = [], [], []
    for one in orders:
        trade, note = await _execute_one(bot, one, action, message)
        if trade is not None:
            trades.append(trade)
        if note:
            notes.append(note)
        legs.append({"ticker": one.get("ticker") or "?",
                     "side": one.get("side"),
                     "executed": trade is not None,
                     "note": note or ""})

    order = {
        "executed": bool(trades),
        "note": "\n".join(notes),
        "side": orders[0].get("side"),
        "ticker": ", ".join(o.get("ticker") or "?" for o in orders),
        "instruction": message[:80],
        # 여러 종목이면 건별 성패를 그대로 싣는다. 화면이 이걸로 몇 건
        # 중 몇 건이 됐는지 한 줄에 보여준다.
        "legs": legs,
        "requested": len(orders),
        "filled": len(trades),
    }
    # 답변에 붙일 문장. 여러 건이면 앞에 요약 한 줄을 세운다.
    note_text = "\n".join(notes)
    if len(orders) > 1:
        note_text = f"주문 {len(orders)}건 중 {len(trades)}건 체결.\n{note_text}"
    return (trades[0] if len(trades) == 1 else (trades or None)), order, note_text


OFF_TOPIC_REPLY = (
    "저는 주식 투자 담당 봇이라 그 주제는 다루지 않습니다. "
    "보유 종목·손익·매매 내역·룰북 설정처럼 투자에 관한 것을 물어봐 주세요.")


def _looks_on_topic(message: str, bot=None) -> bool:
    """모델 없이 주제를 가르는 폴백 판정. 애매하면 통과시킨다.

    [2026-08-02 교정] 예전에는 고정 단어 목록만 봤다. 그래서 "애플 요즘
    상황 어때?" 가 주식 이야기가 아닌 것으로 판정돼 거절당했다 — 봇이
    제일 잘 답해야 할 질문이 제일 먼저 막힌 셈이다.
    종목 이름과 시황을 묻는 말투까지 함께 본다.
    """
    q = message.lower()
    if any(w in q for w in ON_TOPIC_WORDS) or any(w in message for w in NEWS_WORDS):
        return True
    allowed = sorted(bot.rulebook.allowed_tickers) if bot is not None else None
    return bool(MARKETS_.find_tickers(message, allowed))


async def _facts(bot) -> str:
    """봇이 대화에 쓸 재료 전부. 원장·저널·영수증에서만 나온다.

    [왜 통째로 주는가]
    예전에는 질문의 단어를 보고 필요한 조각만 골라 한 문장으로 만들어
    모델에게 넘겼다. 그래서 모델이 할 수 있는 일은 그 문장을 바꿔 쓰는
    것뿐이었고, "왜 그렇게 판단했어?" 처럼 여러 사실을 엮어야 하는 질문에는
    엉뚱한 조각을 받아 바보처럼 답했다.

    이제 상태를 한 번에 다 준다. 무엇이 답에 필요한지는 모델이 고른다.
    숫자의 출처가 여전히 원장 하나라는 점은 그대로다 — 여기 없는 수치는
    지어내지 말라고 프롬프트가 못박는다.
    """
    from app.adapters import universe

    val = await _bot_valuation(bot)
    ret = _return_pct(bot.bot_id, val)
    rate_info = await FX.usd_krw()
    rate = rate_info["rate"]
    rb = bot.rulebook

    def money(micro: int) -> str:
        return f"{micro / USDC:,.2f} USDC ({FX.to_krw(micro, rate):,}원)"

    pct = "집계 전" if ret["pct"] is None else f"{ret['pct']:+.2f}%"
    L = [
        f"환율 1 USD = {rate:,.2f}원 (출처 {rate_info['source']})",
        f"총 자산 {money(val['total_micro'])} "
        f"= 현금 {money(val['cash_micro'])} + 평가액 {money(val['market_micro'])}",
        f"투입원가 {money(val['basis_micro'])} · "
        f"평가손익 {money(val['unrealized_micro'])} · "
        f"실현손익 {money(ret['realized_micro'])} · 총 수익률 {pct}",
        "지갑별 잔고: " + ", ".join(
            f"{r} {val['per_wallet'][r] / USDC:,.2f}" for r in WALLET_ROLES),
        f"룰북: 매매 가능 {', '.join(sorted(rb.allowed_tickers))} · "
        f"확신도 하한 {rb.min_confidence} · 1회 최대 {rb.max_position_usdc / USDC:,.0f} USDC · "
        f"익절 +{rb.take_profit_pct}% / 손절 -{rb.stop_loss_pct}% / "
        f"최대 보유 {rb.max_hold_hours}시간 · 오늘 거래 {bot.trades_today}/{rb.max_trades_per_day}회"
        + (" · 현재 일시정지 상태" if bot.killed else ""),
    ]

    # 열린 포지션 — 종목별 손익과 보유 시간까지. "왜 아직 안 팔았어?" 에
    # 답하려면 청산 조건과 현재 손익을 같이 봐야 한다.
    if val["positions"]:
        for p in val["positions"]:
            price = val["prices"].get(p.ticker, p.entry_price)
            L.append(
                f"보유: {universe.company_name(p.ticker)}({p.ticker}) "
                f"{p.qty / USDC:.6f}주 · 진입 {p.entry_price / USDC:,.2f} → "
                f"현재 {price / USDC:,.2f} USDC · {p.pnl_pct(price):+.2f}% · "
                f"{p.held_hours():.1f}시간 보유")
    else:
        L.append("열린 포지션 없음")

    # 가장 최근 판단의 근거 원문. 이게 없으면 "왜 샀어?" 에 답할 수 없다.
    th = _latest_thesis(bot.bot_id)
    if th is not None:
        receipt = RECEIPTS.receipts.get(th.receipt_id)
        L.append(
            f"최근 판단: {universe.company_name(th.ticker)}({th.ticker}) "
            f"{'매수' if th.side == 'buy' else '매도'} · 확신도 {th.confidence} · "
            f"근거 \"{th.rationale.strip()}\""
            + (f" · 추론 출처 {receipt.inference_sources}" if receipt else ""))

    # 최근 체결 — 무엇을 언제 얼마에 샀고 팔았는지.
    fills = JOURNAL.fills_of(bot.bot_id)[-5:]
    for f in reversed(fills):
        line = (f"체결: {'매수' if f['side'] == 'buy' else '매도'} "
                f"{universe.company_name(f['ticker'])} {f['qty'] / USDC:.6f}주 @ "
                f"{f['price_micro'] / USDC:,.2f} USDC")
        if f["pnl_micro"] is not None:
            line += f" · 손익 {f['pnl_micro'] / USDC:+,.2f} USDC · 사유 {f['reason']}"
        L.append(line)
    if not fills:
        L.append("아직 체결된 거래가 없음")

    # 인지비용 — '스스로 벌어서 스스로 쓴다'는 이 봇의 성질.
    calls = JOURNAL.api_calls_of(bot.bot_id)
    if calls:
        spend = sum(c["amount"] for c in calls)
        L.append(f"API 결제: {len(calls)}회 · 누적 {money(spend)} "
                 f"(판단 한 번에 뉴스·스크리닝·심층추론·시세를 각각 결제)")

    stats = RECEIPTS.stats(bot.bot_id)
    L.append(f"성적: 결정 {len([r for r in RECEIPTS.receipts.values() if r.bot_id == bot.bot_id])}건 · "
             f"적중률 {stats['hit_rate']:.0%}"
             + (" (표본이 적어 보수적 기본값)" if stats["cold_start"] else ""))

    return "\n".join(f"- {x}" for x in L)


# 뉴스를 받아와야 하는 질문인가. 종목 이름이 나오면 당연히 그렇고,
# 이름 없이 "요즘 어때?" 처럼 물어도 시황을 묻는 것이면 그렇다.
NEWS_WORDS = ("뉴스", "호재", "악재", "이슈", "전망", "요즘", "최근", "상황",
              "어때", "어떄", "어떻", "분위기", "시황", "실적", "발표",
              "왜 오르", "왜 내리", "떨어", "올라", "급등", "급락", "사도",
              "살까", "팔까", "괜찮")


async def _news_context(bot, message: str) -> tuple[str, list[str]]:
    """이 질문에 필요한 실제 기사. (프롬프트에 넣을 글, 조회한 종목들)

    [왜 조건부인가]
    뉴스 API 무료 한도가 하루 25회다. 모든 질문에 붙이면 몇 분 만에
    소진되고, 정작 필요한 순간에 못 받는다. 그래서
      · 질문에 종목 이름이 있으면 → 그 종목 (최대 2개)
      · 이름은 없는데 시황을 묻는 것 같으면 → 보유 중인 종목, 없으면
        룰북 첫 종목
      · 둘 다 아니면 → 부르지 않는다 ("내 잔고 얼마야" 에 뉴스는 필요 없다)
    """
    from app.adapters import news
    if not news.enabled():
        return "", []

    allowed = sorted(bot.rulebook.allowed_tickers)
    # 룰북 밖 종목도 찾는다. 매매는 룰북이 막지만 **대화까지 막을 이유는
    # 없다** — "엔비디아 어때?" 에 "제 대상이 아닙니다" 로 끝내면 그건
    # 대화가 아니다. 살 수 없다는 사실은 프롬프트가 따로 말하게 한다.
    wanted = MARKETS_.find_tickers(message)

    if not wanted and any(w in message for w in NEWS_WORDS):
        held = [p.ticker for p in BOOK.of_bot(bot.bot_id)]
        wanted = [(held or allowed)[0]] if (held or allowed) else []

    # 미러 토큰 표기(AAPLx)를 실제 티커로 통일한다. 화면과 프롬프트에
    # 서로 다른 이름이 돌아다니면 모델이 다른 종목으로 읽는다.
    wanted = [t[:-1] if t.endswith("x") else t for t in wanted][:2]
    if not wanted:
        return "", []

    blocks = []
    for t in wanted:
        blocks.append(news.as_text(t, await news.headlines(t)))
    return "\n".join(blocks), wanted


@router.post("/bots/{bot_id}/chat")
async def bot_chat(bot_id: str, req: ChatRequest,
                   session: str = Depends(session_id)):
    """봇과의 대화. **주식·투자 이야기만 한다.**

    답의 재료는 항상 원장·저널에서 나온 사실이다(`_facts`). 모델은 그것을
    해석하고 의견을 말할 수 있지만, **수치는 거기 있는 것만 쓴다.**
    숫자의 출처를 원장 하나로 묶어두려는 것이다.

    주제 제한은 두 겹이다.
      ① 모델이 on_topic 을 함께 판정한다 (app/core/prompts.py 의 지침)
      ② 모델을 못 부르면 단어 기준으로 서버가 직접 가른다
    ②가 없으면 키가 없거나 429일 때 제한이 통째로 사라진다 — 규칙이
    외부 서비스의 가용성에 달려 있으면 그건 규칙이 아니다.
    """
    bot = _get_bot(bot_id, session)
    prof = PROFILES.ensure(bot_id, bot.rulebook.label)

    from app.adapters import gemini_byok, gemini_live
    from app.core.profiles import model_id
    from app.core.prompts import chat_system_prompt

    # ① 진짜 Gemini — 상태를 통째로 주고, 주제 이탈이면 스스로 거절하게 한다.
    if gemini_byok.enabled():
        # 사실은 항상 붙인다. 단어로 미리 거르려다 "너 뭐 살 수 있어?" 같은
        # 멀쩡한 질문에서 사실이 통째로 빠졌다(실측). 주제 판정은 모델이
        # 하고, 단어 목록은 모델을 못 부를 때만 쓴다.
        facts = await _facts(bot)
        # 실제 기사. "애플 호재 있어?" 에 잔고로 답하지 않으려면 이게 있어야 한다.
        news_text, news_tickers = await _news_context(bot, req.message)
        if news_text:
            facts = f"{facts}\n\n[방금 받아온 실제 기사]\n{news_text}"
        try:
            out = await gemini_byok.chat(
                chat_system_prompt(bot, prof), req.message, facts,
                model=model_id(prof.model),
                history=[t.model_dump() for t in req.history])
            reply = out["reply"] if out["on_topic"] else OFF_TOPIC_REPLY
            trade = order = None
            # 모델이 action 을 안 냈어도 사용자가 분명히 지시했으면 집행한다.
            # 모델이 "제 운용 원칙에 맞지 않습니다" 라며 주문을 거절한 적이
            # 있는데, 주문은 모델이 판단할 일이 아니다(_parse_order 주석).
            if out["on_topic"]:
                trade, order, note = await _maybe_execute_chat_order(
                    bot, out.get("action"), req.message)
                if note and trade:
                    # 집행됐으면 결과를 **먼저** 보여준다. 봇의 의견이 앞에
                    # 오면 "매수는 지양하겠습니다 … ✅ 매수했습니다" 처럼
                    # 앞뒤가 어긋나 읽힌다 — 실제로 그렇게 나왔다.
                    reply = (f"{note}\n\n(참고 — 제 판단은 이렇습니다) {reply}")
                elif note:
                    reply = f"{reply}\n\n{note}"
            OWNER_LOG.add("chat", session=session, bot_id=bot_id,
                          name=prof.display_name,
                          detail=req.message[:60]
                                 + ("" if out["on_topic"] else " (주제 밖)")
                                 + (" → 주문 체결" if trade else ""))
            return {"reply": reply,
                    "source": out["model"], "on_topic": out["on_topic"],
                    "news_tickers": news_tickers, "trade": trade,
                    "order": order,
                    "grounded": facts, "suggestions": SUGGESTIONS}
        except Exception as e:                        # noqa: BLE001
            print(f"  ⚠️ 챗 Gemini 실패: {str(e)[:90]} → 상태 기반 응답 사용")

    # ② 폴백 — 모델 없이 답한다. 여기서도 주제 제한은 그대로 산다.
    if not _looks_on_topic(req.message, bot):
        OWNER_LOG.add("chat", session=session, bot_id=bot_id,
                      name=prof.display_name,
                      detail=req.message[:60] + " (주제 밖)")
        return {"reply": OFF_TOPIC_REPLY, "source": "state", "on_topic": False,
                "suggestions": SUGGESTIONS}

    grounded = await _answer_from_state(bot, req.message)

    # 모델을 못 불러도 **주문은 집행한다.**
    #
    # [빠져 있던 것 — 2026-08-03] 주문 집행이 위쪽 gemini_byok 블록 안에만
    # 있었다. 그래서 키가 없거나 429·타임아웃이 나면 "NVDA 사줘" 가 조용히
    # 그냥 질문이 됐다 — 답변은 멀쩡히 오니까 사용자는 실행된 줄 안다.
    # _parse_order 는 순수한 서버 코드고 Gemini 와 아무 상관이 없다.
    # 주문이 외부 서비스의 가용성에 달려 있으면 그건 주문 접수가 아니다.
    trade, order, note = await _maybe_execute_chat_order(bot, None, req.message)

    def _with_note(text: str) -> str:
        """집행됐으면 결과를 먼저, 아니면 답변 뒤에 사유를 붙인다.
        (위쪽 gemini_byok 경로와 같은 규칙 — 순서가 다르면 읽는 사람이
         같은 화면에서 다른 관습을 두 번 배워야 한다.)"""
        if not note:
            return text
        return f"{note}\n\n{text}" if trade else f"{text}\n\n{note}"

    if gemini_live.enabled():
        prompt = (
            f"당신은 사용자의 자동매매 봇 '{prof.display_name or bot_id}'입니다.\n"
            f"봇 성격: {prof.prompt or '지정 없음'}\n"
            f"사용자 질문: {req.message}\n"
            f"서버가 확인한 사실: {grounded}\n\n"
            f"위 사실만 근거로 2~3문장 한국어로 답하세요. "
            f"주식·투자 외의 주제는 답하지 마세요. "
            f"사실에 없는 숫자를 지어내지 마세요.")
        try:
            text = await gemini_live.converse_async(prompt)
            OWNER_LOG.add("chat", session=session, bot_id=bot_id,
                          name=prof.display_name,
                          detail=req.message[:60] + (" → 주문 체결" if trade else ""))
            return {"reply": _with_note(text.strip()), "source": "gemini-live",
                    "on_topic": True, "grounded": grounded,
                    "trade": trade, "order": order,
                    "suggestions": SUGGESTIONS}
        except Exception as e:                        # noqa: BLE001
            print(f"  ⚠️ 챗 Gemini 실패: {str(e)[:90]} → 상태 기반 응답 사용")

    OWNER_LOG.add("chat", session=session, bot_id=bot_id,
                  name=prof.display_name,
                  detail=req.message[:60] + " (원장 기반 응답)"
                         + (" → 주문 체결" if trade else ""))
    return {"reply": _with_note(grounded), "source": "state", "on_topic": True,
            "trade": trade, "order": order, "suggestions": SUGGESTIONS}


# ══════ 봇 삭제 ═══════════════════════════════════════════════════
@router.get("/bots/{bot_id}/delete-preflight")
async def delete_preflight(bot_id: str, session: str = Depends(session_id)):
    """지우기 전에 무슨 일이 일어나는지 먼저 알려준다.

    화면이 확인 창에 그대로 띄운다. '삭제하면 뭐가 사라지는지'를 모른 채
    누르게 하지 않으려는 것이다.
    """
    bot = _get_bot(bot_id, session)
    rate_info = await FX.usd_krw()
    val = await _bot_valuation(bot)
    positions = val["positions"]

    return {
        **_bot_header(bot),
        "open_positions": [
            {"ticker": p.ticker, "qty": round(p.qty / USDC, 6),
             "basis_micro": p.basis,
             "pnl_pct": round(p.pnl_pct(val["prices"].get(p.ticker,
                                                          p.entry_price)), 2)}
            for p in positions
        ],
        "will_close_first": len(positions),
        "balances": val["per_wallet"],
        "remaining_micro": val["cash_micro"],
        "remaining_krw": FX.to_krw(val["cash_micro"], rate_info["rate"]),
        "fills": len(JOURNAL.fills_of(bot_id)),
        # ⚠️ 이 시스템에는 출금(user-treasury → external) 경로가 없다.
        #    core/routes.py 의 화이트리스트에 그 조합이 없고, audit.py 6번이
        #    "사용자 원금 유출 차단"으로 그걸 검증한다. 그래서 봇을 지워도
        #    잔액은 그 봇의 지갑에 남는다. 숨기지 않고 화면에 띄운다.
        "withdrawal_supported": False,
        "note": "삭제해도 잔액은 이 봇의 지갑에 남습니다 — 이 데모에는 출금 경로가 "
                "없습니다(사용자 원금 유출을 막는 규칙과 같은 이유).",
    }


@router.delete("/bots/{bot_id}")
async def delete_bot_ui(bot_id: str, close_positions: bool = True,
                        _: None = Depends(require_admin),
                        session: str = Depends(session_id)):
    """봇을 지운다. 열린 포지션이 있으면 먼저 청산한다.

    main.delete_bot 은 포지션이 남아 있으면 409로 거절한다 — 청산할 주체가
    사라진 포지션은 영영 닫히지 않기 때문이다. 여기서는 그 앞단계를
    대신 밟아준다. 청산 자체는 기존 /close-all 라우트가 한다.
    """
    from app.main import close_all, delete_bot
    _get_bot(bot_id, session)

    # 지우기 전에 이름을 챙긴다 — 지운 뒤에는 프로필도 함께 사라져서
    # 로그에 "(이름 없음)" 만 남는다.
    name = PROFILES.ensure(bot_id).display_name
    calls = len(JOURNAL.api_calls_of(bot_id))
    fills = len(JOURNAL.fills_of(bot_id))

    closed = 0
    if close_positions and BOOK.of_bot(bot_id):
        result = await close_all(bot_id, None)
        closed = result["closed"]

    deleted = await delete_bot(bot_id, None)
    OWNER_LOG.add("bot_deleted", session=session, bot_id=bot_id, name=name,
                  detail=f"거래 {fills}건 · API {calls}회 · 청산 {closed}건")
    return {**deleted, "closed_positions": closed}


# ══════ 봇 정지·재개 ═══════════════════════════════════════════════
@router.post("/bots/{bot_id}/pause")
async def pause_bot(bot_id: str, on: bool = True,
                    _: None = Depends(require_admin),
                    session: str = Depends(session_id)):
    """요약 탭의 '일시 정지'. main.kill_bot 과 같은 스위치를 누른다.

    tracker.policy.killed 까지 함께 세워야 진짜로 멈춘다 — 그게 없으면
    스케줄러는 건너뛰지만 수동 사이클은 계속 결제를 시도한다.
    """
    from app.core import store as STORE
    bot = _get_bot(bot_id, session)
    bot.killed = on
    bot.tracker.policy.killed = on
    STORE.save()
    return {"bot_id": bot_id, "killed": on}


@router.get("/bots/{bot_id}/chat/suggestions")
async def chat_suggestions(bot_id: str, session: str = Depends(session_id)):
    _get_bot(bot_id, session)
    return {"suggestions": SUGGESTIONS}


# ══════ 알림 (상단 팝업) ═══════════════════════════════════════════
@router.get("/events")
async def events(since: int = -1, session: str = Depends(session_id)):
    """체결 알림 피드.

    화면은 마지막으로 본 seq 만 기억하고 그 뒤엣것만 받아간다.
    since 를 안 주면(-1) 지금 seq 만 알려주고 과거는 보내지 않는다 —
    화면을 열자마자 지난 체결이 우르르 뜨면 그건 알림이 아니라 소음이다.
    """
    from app.core.events import EVENTS
    if since < 0:
        return {"seq": EVENTS.seq, "events": []}
    # 남의 봇 체결이 내 화면에 뜨면 안 된다. 세션이 볼 수 있는 봇만 남긴다.
    mine = {b.bot_id for b in _my_bots(session)}
    rows = [e for e in EVENTS.since(since) if e.get("bot_id") in mine]
    rate_info = await FX.usd_krw()
    for r in rows:
        if r["kind"] == "fill":
            r["gross_krw"] = FX.to_krw(r.get("gross_micro", 0), rate_info["rate"])
        elif r["kind"] == "deposit":
            r["amount_krw"] = FX.to_krw(r.get("amount_micro", 0), rate_info["rate"])
        r["explorer"] = _explorer(r.get("tx", ""))
    return {"seq": EVENTS.seq, "events": rows}


async def _pull_from_judge(bot_id: str, session: str,
                           draw: int | None = None) -> dict | None:
    """심사위원 지갑에서 봇 트레저리로 위임 인출. 위임이 없으면 아무것도 안 한다.

    [왜 지갑이 둘인가 — 자주 나오는 질문]
    심사위원 지갑은 **우리가 개인키를 갖지 않는** 외부 지갑이다. 거기서
    돈을 빼려면 본인 서명이 있거나, 미리 위임(SPL approve)을 받아야 한다.
    반면 봇 지갑은 우리가 키를 들고 있어서 API 결제·매수·정산을 사람에게
    묻지 않고 집행할 수 있다.

    그래서 둘을 없앨 수는 없지만 **나눠 보일 이유도 없다.** 위임이 있으면
    앱의 사이클도 여기서 먼저 끌어오고, 그 인출에 서명이 필요 없다는 사실이
    로그에 그대로 남는다. 위임이 없으면 봇 지갑 안에서 돈다.
    """
    from app.judge import DEFAULT_DRAW, _restore, judge_wallet

    if not hasattr(LEDGER, "delegated_transfer"):
        return None                       # mock 원장에는 위임 개념이 없다
    # 재시작으로 등록이 날아갔으면 저장해둔 주소로 되살린다.
    if not await _restore(session):
        return None

    wallet = judge_wallet(session)
    try:
        st = await LEDGER.delegate_status(wallet)
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 위임 상태 조회 실패: {str(e)[:80]}")
        return None

    amount = min(draw or DEFAULT_DRAW, st.get("allowance", 0),
                 st.get("balance", 0))
    if amount <= 0:
        return None

    try:
        proof = await LEDGER.delegated_transfer(
            wallet, f"user-treasury@{bot_id}", amount, "app-run-deposit")
    except Exception as e:                                # noqa: BLE001
        print(f"  ⚠️ 위임 인출 실패: {str(e)[:100]}")
        return None

    return {"step": "pull-from-wallet",
            "amount": amount,
            "amount_usd": round(amount / USDC, 2),
            "note": "심사위원 지갑에서 인출 — 추가 서명 없음 (위임 권한)",
            "tx": proof.proof_id, "explorer": _explorer(proof.proof_id)}


# ══════ 지금 일해보기 ══════════════════════════════════════════════
@router.post("/bots/{bot_id}/run")
async def run_cycle(bot_id: str, attempts: int = 2,
                    _: None = Depends(require_admin),
                    session: str = Depends(session_id)):
    """앱 안에서 봇을 한 사이클 돌린다.

    `POST /bots/{id}/cycle` 을 그대로 부르되 두 가지를 앞에 붙인다.

      · 시연 반복으로 쌓인 상태만 리셋 (`judge._preflight`) — 일일 거래
        한도·인지비용 일일 상한·킬 스위치. 룰북과 만다트 기준은 건드리지
        않는다. 무엇을 되돌렸는지는 응답의 preflight 에 그대로 실린다.
      · 룰북 허용 종목의 뉴스가 오도록 편향 (`external.demo_bias`).
        판단·룰북·체결은 손대지 않는다. 재료만 보장하고 결정은 그대로 시킨다.

    체결까지 못 가면 attempts 회까지 다시 돌린다. 뉴스의 신규성이 난수라
    한 번에 안 붙는 경우가 실제로 있고, 그때마다 시연이 끝나면 곤란하다.
    각 시도는 진짜 사이클이라 API 결제도 그만큼 실제로 일어난다.
    """
    import httpx
    from app.external import demo_bias
    from app.judge import _preflight

    bot = _get_bot(bot_id, session)
    notes = _preflight(bot)

    # [2026-08-03] 심사위원 지갑이 위임돼 있으면 **거기서 먼저 끌어온다.**
    #
    # 예전에는 이 버튼이 봇 지갑의 돈만 썼고, 심사위원 지갑에서 빠져나가는
    # 것은 옆 패널의 ④ 버튼에서만 볼 수 있었다. 같은 사이클인데 자금 출처만
    # 다른 두 버튼이 있으니 "왜 나뉘어 있냐"는 질문이 나왔다 — 나뉠 이유가
    # 없다. 위임이 있으면 앱 버튼도 심사위원 지갑에서 끌어온다.
    #
    # 위임이 없으면 그대로 봇 지갑으로 돈다. 지갑을 안 붙인 사람도
    # 앱만으로 전 과정을 볼 수 있어야 하기 때문이다.
    pulled = await _pull_from_judge(bot_id, session)
    if pulled:
        notes = [*notes, pulled]

    from app.main import BASE, INTERNAL_HEADERS, app as fastapi_app
    transport = httpx.ASGITransport(app=fastapi_app)

    log: list[dict] = []
    filled = False
    tried = 0
    for attempt in range(1, max(1, attempts) + 1):
        tried = attempt
        with demo_bias(bot.rulebook.allowed_tickers):
            async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                         headers=INTERNAL_HEADERS,
                                         timeout=300.0) as client:
                res = await client.post(f"{BASE}/bots/{bot_id}/cycle")
        if res.status_code != 200:
            raise HTTPException(res.status_code, {
                "error": "사이클 실패", "detail": res.text[:200]})
        log = res.json().get("log", [])
        filled = any(s.get("step") == "executor" and "qty" in s for s in log)
        if filled:
            break

    OWNER_LOG.add("run", session=session, bot_id=bot_id,
                  name=PROFILES.ensure(bot_id).display_name,
                  detail=("체결됨" if filled else "체결 없음")
                         + f" · 시도 {tried}회")
    if filled:
        ex = next(s for s in log if s.get("step") == "executor" and "qty" in s)
        from app.adapters import universe
        OWNER_LOG.add("fill", session=session, bot_id=bot_id,
                      name=PROFILES.ensure(bot_id).display_name,
                      detail=f"{universe.company_name(ex['ticker'])} "
                             f"{ex['qty']:.6f}주 @ ${ex['entry_price']:,.2f}")
    return {"bot_id": bot_id, "filled": filled, "attempts": tried,
            "preflight": notes, "log": log,
            "balances": await bot.balances()}


@router.post("/bots/{bot_id}/sell")
async def sell_positions(bot_id: str, all: bool = False,
                         _: None = Depends(require_admin),
                         session: str = Depends(session_id)):
    """매도·정산.

      all=false  룰북 조건(익절·손절·최대보유)에 걸린 것만 청산한다.
                 이게 봇이 평소에 하는 일이고, 스케줄러가 매 주기 부른다.
      all=true   조건과 무관하게 전량 청산한다. "지금 다 팔아" 버튼.

    어느 쪽이든 청산 → 정산 → 분배(85/10/5)가 실제로 일어나고, 그
    분배는 매수 시점 영수증에 박제된 비율을 그대로 쓴다. 손익은
    진입가와 청산가의 차이에서 나온다 — 여기서 만들어내지 않는다.
    """
    import httpx
    from app.main import BASE, INTERNAL_HEADERS, app as fastapi_app

    bot = _get_bot(bot_id, session)
    open_before = len(BOOK.of_bot(bot_id))
    if open_before == 0:
        return {"bot_id": bot_id, "closed": 0, "sold": [],
                "realized_micro": 0, "open_before": 0,
                "note": "열린 포지션이 없습니다."}

    transport = httpx.ASGITransport(app=fastapi_app)
    path = f"{BASE}/bots/{bot_id}/" + ("close-all" if all else "manage-positions")
    async with httpx.AsyncClient(transport=transport, base_url=BASE,
                                 headers=INTERNAL_HEADERS, timeout=300.0) as c:
        r = await c.post(path)
    if r.status_code != 200:
        raise HTTPException(r.status_code, {
            "error": "청산 실패", "detail": r.text[:200]})
    body = r.json()

    # 두 라우트의 응답 모양이 다르다. 화면이 한 가지만 알면 되도록 여기서 편다.
    results = (body.get("results", []) if all
               else [a["settle"] for a in body.get("actions", [])
                     if a.get("closed") and isinstance(a.get("settle"), dict)])

    rate = (await FX.usd_krw())["rate"]
    from app.adapters import universe
    sold = []
    realized = 0
    for res in results:
        if not isinstance(res, dict):
            continue
        pnl = res.get("realized_pnl") or 0
        realized += pnl
        sold.append({
            "ticker": res.get("ticker"),
            "company": universe.company_name(res.get("ticker") or ""),
            "flag": MARKETS_.flag(res.get("ticker") or ""),
            "qty": res.get("qty"),
            "entry_price": res.get("entry_price"),
            "exit_price": res.get("exit_price"),
            "held_hours": res.get("held_hours"),
            "realized_micro": pnl,
            "realized_krw": FX.to_krw(pnl, rate),
            "distribution": res.get("distribution"),
            "tx": res.get("close_tx"),
            "explorer": _explorer(res.get("close_tx") or ""),
        })

    OWNER_LOG.add("sell", session=session, bot_id=bot_id,
                  name=PROFILES.ensure(bot_id).display_name,
                  detail=f"{len(sold)}건 청산 · 실현손익 "
                         f"{realized / 1e6:+.2f} USDC")
    return {
        "bot_id": bot_id,
        "mode": "all" if all else "rulebook",
        "open_before": open_before,
        "open_after": len(BOOK.of_bot(bot_id)),
        "closed": len(sold),
        "sold": sold,
        "realized_micro": realized,
        "realized_krw": FX.to_krw(realized, rate),
        "balances": await bot.balances(),
        # 조건에 안 걸린 포지션도 그대로 알려준다 — 왜 안 팔렸는지가
        # 팔린 것만큼 중요하다.
        "checked": body.get("actions") if not all else None,
    }


# ══════ 설정 ═══════════════════════════════════════════════════════
@router.get("/meta")
async def meta():
    """설정 화면이 쓰는 선택지 전부. 프론트에 목록을 박아두지 않는다."""
    from app.adapters import universe
    try:
        stocks = universe.load()["stocks"]
    except Exception:                                  # noqa: BLE001
        stocks = {}
    # 미러 토큰은 접미사 x가 붙는다. 화면에는 원 티커와 회사명을 보여주고
    # 서버로는 원 티커를 보낸다 (create_bot 이 x를 붙인다).
    catalog = [{"ticker": t, "company": c} for t, c in sorted(stocks.items())]

    return {
        "tags": TAGS, "styles": STYLES, "goals": GOALS, "risks": RISKS,
        # 시장은 이름만이 아니라 '무엇이 상장돼 있고 거래 가능한가'까지
        # 함께 보낸다. 화면이 거래 불가 시장을 회색으로 두고 이유를
        # 그대로 띄울 수 있어야 한다.
        "markets": MARKETS_.MARKETS,
        "sessions": SESSIONS, "currencies": CURRENCIES,
        "models": MODELS,
        "universe": catalog,
        "universe_count": len(catalog),
        "limits": {
            "name": 20, "tagline": 50, "prompt": 500,
            "max_trades_per_day": 100,
        },
        "defaults": {
            "min_confidence": 0.8, "max_position_usd": 50.0,
            "max_trades_per_day": 5, "take_profit_pct": 5.0,
            "stop_loss_pct": 3.0, "max_hold_hours": 24.0,
            "deposit_usdc": 500.0,
        },
        "fx": await FX.usd_krw(),
    }


def _tickers_from(market_keys: list[str], explicit: list[str]) -> list[str]:
    """고른 시장 → 룰북에 들어갈 허용 종목 목록.

    종목을 직접 준 호출(스크립트·옛 경로)은 그 값을 그대로 존중한다.
    화면에서 오는 요청에는 종목이 없고 시장만 있다.

    거래 불가 시장을 고르면 여기서 막는다. 통과시키면 룰북이 빈 목록이
    되고, 그 봇은 이유를 모른 채 영원히 아무것도 사지 못한다.
    """
    if explicit:
        return explicit

    tickers = MARKETS_.tickers_for(market_keys)
    if tickers:
        # 거래 가능한 시장이 하나라도 있으면 통과시킨다. 섞여 있을 때
        # 통째로 거절하면, 예전 프로필(나라 이름을 들고 있던)을 그냥
        # 저장하려는 사람이 이유도 모른 채 막힌다.
        return tickers

    bad = MARKETS_.untradable(market_keys)
    raise HTTPException(400, {
        "error": "아직 거래할 수 없는 시장",
        "markets": MARKETS_.names_for(bad) or market_keys,
        "hint": " / ".join(MARKETS_.BY_KEY[k]["note"]
                           for k in bad if k in MARKETS_.BY_KEY)
                or "devnet 에 미러 주식 토큰이 없습니다. 거래 가능한 시장을 "
                   "하나 이상 골라주세요."})


class UiBotRequest(BaseModel):
    """설정 화면이 보내는 것 전부 — 룰북과 프로필이 한 번에 온다."""
    # 프로필
    display_name: str = Field(..., min_length=1, max_length=20)
    tagline: str = Field("", max_length=50)
    prompt: str = Field("", max_length=500)
    tags: list[str] = ["주식"]
    style: str = STYLES[0]
    # 시장 키 (app/core/markets.py). 사용자는 '어느 장에서 놀지'만 고르고
    # 그 안에서 무엇을 살지는 봇이 정한다.
    markets: list[str] = Field(default_factory=lambda: list(DEFAULT_MARKETS),
                               min_length=1)
    session: str = SESSIONS[0]
    base_currency: str = CURRENCIES[0]
    goal: str = GOALS[0]
    risk: str = RISKS[2]
    notify: bool = True
    auto_reinvest: bool = False
    grant_more_authority: bool = False
    model: str = MODELS[0]

    # 룰북 (집행되는 것)
    owner: str = "사용자"
    deposit_usdc: float = Field(500.0, gt=0)
    # 개별 종목은 더 이상 화면에서 받지 않는다 — markets 에서 펼쳐진다.
    # 필드를 남겨둔 이유는 스크립트(verify_scenario.py 등)와 옛 호출이
    # 종목을 직접 지정하는 경로를 계속 쓰기 때문이다.
    tickers: list[str] = []
    min_confidence: float = Field(0.8, ge=0.0, le=1.0)
    max_position_usd: float = Field(50.0, gt=0)
    max_trades_per_day: int = Field(5, ge=1, le=100)
    take_profit_pct: float = Field(5.0, gt=0)
    stop_loss_pct: float = Field(5.0, gt=0)
    max_hold_hours: float = Field(24.0, gt=0)

    def to_profile(self, bot_id: str) -> BotProfile:
        return BotProfile(
            bot_id=bot_id, display_name=self.display_name,
            tagline=self.tagline, prompt=self.prompt, tags=self.tags,
            style=self.style, markets=self.markets, session=self.session,
            base_currency=self.base_currency, goal=self.goal, risk=self.risk,
            notify=self.notify, auto_reinvest=self.auto_reinvest,
            grant_more_authority=self.grant_more_authority, model=self.model)


@router.post("/bots")
async def create_bot_ui(req: UiBotRequest, _: None = Depends(require_admin),
                        session: str = Depends(session_id)):
    """봇 생성. 룰북은 main.create_bot 이, 프로필은 여기가 맡는다.

    create_bot 을 함수로 직접 부른다. 종목 검증·지갑 생성·예치·부분 실패
    처리가 전부 거기 있고, 그걸 여기에 베껴 쓰면 두 벌이 어긋나는 순간
    한쪽 경로로만 고아 봇이 생긴다. 인증은 이 라우트가 이미 했으므로
    두 번째 인자에는 None 을 넘긴다 (Depends 결과 자리라 쓰이지 않는다).
    """
    from app.main import CreateBotRequest, create_bot
    from app.core import store as STORE

    tickers = _tickers_from(req.markets, req.tickers)

    core = CreateBotRequest(
        owner=req.owner, label=req.display_name,
        deposit_usdc=req.deposit_usdc, tickers=tickers,
        min_confidence=req.min_confidence,
        max_position_usd=req.max_position_usd,
        max_trades_per_day=req.max_trades_per_day,
        take_profit_pct=req.take_profit_pct,
        stop_loss_pct=req.stop_loss_pct,
        max_hold_hours=req.max_hold_hours)

    # 세션을 그대로 넘긴다 — 이 봇의 주인이 곧 이 브라우저다.
    created = await create_bot(core, None, session)
    PROFILES.put(req.to_profile(created["bot_id"]))
    STORE.save()

    # 만들어진 봇이 Gemini 에게 무엇을 지시받는지 같이 돌려준다.
    # 사용자가 방금 정한 룰북이 어떤 문장이 되었는지 그 자리에서 보여주려는
    # 것이다 — 나중에 설정 화면에서도 같은 값을 다시 볼 수 있다.
    bundle = await get_profile(created["bot_id"], session)
    prof = bundle["profile"]
    OWNER_LOG.add("bot_created", session=session, bot_id=created["bot_id"],
                  name=prof.get("display_name", ""),
                  detail=f"{' · '.join(bundle['market_names'])} · "
                         f"{len(bundle['rulebook']['tickers'])}종목 · "
                         f"확신도 하한 {bundle['rulebook']['min_confidence']}")
    return {**created, "profile": prof, "ai": bundle["ai"]}


@router.get("/bots/{bot_id}/profile")
async def get_profile(bot_id: str, session: str = Depends(session_id)):
    from app.adapters import gemini_byok
    from app.core.profiles import model_id
    from app.core.prompts import trading_system_prompt

    bot = _get_bot(bot_id, session)
    prof = PROFILES.ensure(bot_id, bot.rulebook.label)
    rb = bot.rulebook
    return {
        "profile": prof.model_dump(),
        "badge": prof.badge,
        # 이 봇이 Gemini 에게 실제로 주는 지침. 룰북·프로필에서 그때그때
        # 조립되므로(app/core/prompts.py) 설정을 고치면 이 값도 같이 바뀐다 —
        # 화면에 보이는 지침과 실제로 쓰이는 지침이 어긋날 수 없다.
        "ai": {
            "system_prompt": trading_system_prompt(bot, prof),
            "model": prof.model,
            "model_id": model_id(prof.model) or config.GEMINI_DEEP_MODEL,
            "live": gemini_byok.enabled(),
            "inference_mode": config.INFERENCE_MODE,
        },
        # 시장 이름을 함께 준다. 프로필에는 키만 저장되므로, 화면이
        # 이름표를 다시 만들다 카탈로그와 어긋나는 일을 막는다.
        "market_names": MARKETS_.names_for(prof.markets),
        "rulebook": {
            "tickers": sorted(t[:-1] if t.endswith("x") else t
                              for t in rb.allowed_tickers),
            "min_confidence": rb.min_confidence,
            "max_position_usd": rb.max_position_usdc / USDC,
            "max_trades_per_day": rb.max_trades_per_day,
            "take_profit_pct": rb.take_profit_pct,
            "stop_loss_pct": rb.stop_loss_pct,
            "max_hold_hours": rb.max_hold_hours,
        },
        "killed": bot.killed,
    }


class ProfilePatch(BaseModel):
    display_name: str | None = Field(None, max_length=20)
    tagline: str | None = Field(None, max_length=50)
    prompt: str | None = Field(None, max_length=500)
    tags: list[str] | None = None
    style: str | None = None
    markets: list[str] | None = None
    session: str | None = None
    base_currency: str | None = None
    goal: str | None = None
    risk: str | None = None
    notify: bool | None = None
    auto_reinvest: bool | None = None
    grant_more_authority: bool | None = None
    model: str | None = None

    # 룰북 쪽도 함께 고칠 수 있다. 집행되는 값이라 별도로 표시해 둔다.
    tickers: list[str] | None = Field(None, min_length=1)
    min_confidence: float | None = Field(None, ge=0.0, le=1.0)
    max_position_usd: float | None = Field(None, gt=0)
    max_trades_per_day: int | None = Field(None, ge=1, le=100)
    take_profit_pct: float | None = Field(None, gt=0)
    stop_loss_pct: float | None = Field(None, gt=0)
    max_hold_hours: float | None = Field(None, gt=0)


@router.patch("/bots/{bot_id}/profile")
async def patch_profile(bot_id: str, patch: ProfilePatch,
                        _: None = Depends(require_admin),
                        session: str = Depends(session_id)):
    from app.adapters import universe
    from app.core import store as STORE
    bot = _get_bot(bot_id, session)
    prof = PROFILES.ensure(bot_id, bot.rulebook.label)

    data = patch.model_dump(exclude_none=True)
    rb_keys = {"tickers", "min_confidence", "max_position_usd",
               "max_trades_per_day", "take_profit_pct", "stop_loss_pct",
               "max_hold_hours"}

    for k, v in data.items():
        if k in rb_keys:
            continue
        setattr(prof, k, v)
    PROFILES.put(prof)

    # 룰북은 봇 인스턴스에 직접 반영한다. 이 값들은 다음 사이클부터
    # 실제로 집행된다 — 이미 열린 포지션의 청산 조건도 함께 바뀐다.
    rb = bot.rulebook

    # 시장을 바꾸면 룰북의 허용 종목도 따라 바뀐다. 둘을 따로 두면
    # "코스피로 바꿨는데 여전히 NVDA만 산다" 같은 어긋남이 생긴다.
    if "markets" in data and "tickers" not in data:
        data["tickers"] = _tickers_from(data["markets"], [])

    if "tickers" in data:
        # 생성 때와 같은 검증을 통과해야 한다. 여기만 느슨하면 수정으로
        # 존재하지 않는 종목을 넣을 수 있고, 그 봇은 이유를 모른 채
        # 영원히 아무것도 못 산다.
        wanted = {t if t.endswith("x") else f"{t}x" for t in data["tickers"]}
        unknown = [t for t in wanted if not universe.is_tracked(t)]
        if unknown:
            raise HTTPException(400, {
                "error": "추적 대상이 아닌 종목", "unknown": unknown,
                "hint": "smartmoney.market SEC 추적 목록에 없습니다."})
        # 이미 들고 있는 종목을 목록에서 빼면 그 포지션은 청산 규칙만
        # 적용받는 미아가 된다. 팔 수는 있으니 막지는 않되 알려준다.
        held = {p.ticker for p in BOOK.of_bot(bot_id)}
        rb.allowed_tickers = wanted
        orphaned = sorted(held - wanted)
    else:
        orphaned = []

    if "min_confidence" in data:
        rb.min_confidence = data["min_confidence"]
    if "max_position_usd" in data:
        rb.max_position_usdc = int(data["max_position_usd"] * USDC)
    if "max_trades_per_day" in data:
        rb.max_trades_per_day = data["max_trades_per_day"]
    if "take_profit_pct" in data:
        rb.take_profit_pct = data["take_profit_pct"]
    if "stop_loss_pct" in data:
        rb.stop_loss_pct = data["stop_loss_pct"]
    if "max_hold_hours" in data:
        rb.max_hold_hours = data["max_hold_hours"]
    if "display_name" in data:
        rb.label = data["display_name"]

    STORE.save()
    return {**await get_profile(bot_id, session), "orphaned_positions": orphaned}
