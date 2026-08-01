"""재시작을 넘겨 살아남아야 하는 상태.

[왜 필요한가]
BOTS·RECEIPTS·BOOK은 전부 메모리에만 있었다. 서버를 껐다 켜면 사용자가
만든 봇이 사라진다. mock에서는 데모가 초기화되는 정도지만, devnet에서는
그 봇의 온체인 USDC와 미러 토큰이 남은 채 **그것을 움직일 수 있는 주체가
사라진다** = 자금 좌초. 실제로 wallets/ 에 주인 없는 봇 지갑이 쌓여 있었다.

[무엇을 저장하나]
  bots       룰북·소유자·생성시각·일일 카운터·킬 상태
  receipts   정산 분배표(splits_bps)가 여기 있다. 포지션만 복원하고
             영수증을 버리면 settle()이 KeyError로 죽는다. 세트로 묶여 있다.
  positions  열린 포지션. 복원하지 않으면 온체인에는 토큰이 있는데 시스템은
             모르는 상태가 된다 — 룰북이 영원히 청산 조건을 검사하지 않는
             자금. 이게 이 파일을 만든 가장 큰 이유다.
  anchors    앵커 로그 (트랙레코드의 연속성)

[무엇을 저장하지 않나]
  키페어           wallets/*.json 이 이미 갖고 있다. 두 벌 만들지 않는다.
  SIGNALS/THESES   판매 창구용 휘발성 카탈로그. 사라져도 자금이 묶이지 않고,
                   재시작 후 다음 사이클에서 새로 만들어진다.
  잔고             원장이 스스로 책임진다(dump_state/load_state).
                   devnet 원장에는 그 메서드가 없다 — 진실원이 온체인이라
                   복제하면 두 값이 어긋나는 순간 어느 쪽이 맞는지 알 수
                   없게 된다. mock은 메모리가 진실원이라 스스로 남긴다.

[쓰기 방식]
tmp 파일에 쓰고 원자적으로 교체한다. 쓰는 도중에 프로세스가 죽어도
직전 상태가 온전히 남는다. 반쯤 쓰인 상태 파일로 복원하는 것이
복원하지 않는 것보다 나쁘다.
"""
from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import threading

# ── 상태 파일 경로 ──────────────────────────────────────────────────
# [2026-08] 원장 모드별로 파일을 나눈다.
#
# 예전에는 mock이든 devnet이든 같은 파일을 썼다. devnet에서 포지션을
# 연 뒤 mock으로 기동하면, 봇과 포지션은 복원되는데 mock 원장은 빈
# 상태라 청산이 "NVDAx 보유 부족: 0 < 52208" 로 실패했다 — 자금이 묶인다.
# (실측 확인. 반대 방향은 load()의 _servable 가드가 막고 있었지만
#  mock 원장에는 판별할 단서 자체가 없어 그 가드가 안 통한다.)
#
# 경로를 나눠 애초에 섞이지 않게 하고, 파일 안에도 모드를 적어
# 손으로 옮겼을 때의 마지막 방어선을 둔다.
#
# 검증 스크립트는 STATE_FILE로 따로 지정한다. 운영 상태와 파일을
# 공유하면 실행할 때마다 앞선 실행의 봇이 되살아나 누적된다.
_MODE = os.environ.get("LEDGER_MODE", "mock").lower()
LEGACY_PATH = pathlib.Path("state/app_state.json")   # 모드 분리 이전 파일
STATE_PATH = pathlib.Path(
    os.environ.get("STATE_FILE", f"state/app_state.{_MODE}.json"))
VERSION = 1

_lock = threading.Lock()


def _atomic_write(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def save() -> None:
    """현재 상태를 파일에 남긴다. 상태를 바꾸는 라우트마다 호출한다.

    실패해도 예외를 밖으로 던지지 않는다 — 상태 저장이 안 됐다고
    진행 중인 거래를 중단시키면 더 나쁜 상태가 된다. 대신 시끄럽게 남긴다.
    """
    from app.bots import BOTS
    from app.core.ledger import LEDGER
    from app.core.positions import BOOK
    from app.core.receipts import RECEIPTS

    try:
        with _lock:
            _atomic_write(STATE_PATH, {
                "version": VERSION,
                # 어느 원장에서 나온 상태인지 남긴다. 이게 없으면
                # devnet 상태를 mock이 복원해도 아무도 못 알아챈다.
                "ledger_mode": _MODE,
                # 원장이 스스로 남길 수 있으면 남긴다(mock). devnet은
                # 온체인이 진실원이라 이 메서드가 없고, 없는 게 맞다.
                "ledger": (LEDGER.dump_state()
                           if hasattr(LEDGER, "dump_state") else None),
                "bots": [{
                    "bot_id": b.bot_id,
                    "owner": b.owner,
                    "rulebook": b.rulebook.model_dump(mode="json"),
                    "created_at": b.created_at,
                    "trades_today": b.trades_today,
                    "trade_day": b._trade_day,
                    "killed": b.killed,
                } for b in BOTS.values()],
                "receipts": [r.model_dump(mode="json")
                             for r in RECEIPTS.receipts.values()],
                "positions": [dataclasses.asdict(p) for p in BOOK.all()],
                "anchors": RECEIPTS.anchors,
            })
    except Exception as e:                        # noqa: BLE001
        print(f"  ⚠️ 상태 저장 실패({STATE_PATH}): {str(e)[:120]}")


def _migrate_legacy() -> pathlib.Path | None:
    """모드 분리 이전 파일(state/app_state.json)을 한 번만 넘겨받는다.

    그 파일에는 ledger_mode가 없지만 어느 모드였는지 알 수 있다.
    mock 원장만 dump_state를 갖고 있으므로, ledger 필드가 dict면 mock,
    None이면 devnet에서 저장된 것이다. 추측이 아니라 구조가 말해준다.

    현재 모드와 맞을 때만 새 이름으로 옮긴다. 안 맞으면 건드리지
    않는다 — 나중에 그 모드로 기동하면 그때 옮겨진다.
    """
    if STATE_PATH != pathlib.Path(f"state/app_state.{_MODE}.json"):
        return None                     # STATE_FILE을 명시한 경우엔 관여 안 함
    if not LEGACY_PATH.exists():
        return None
    try:
        data = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except Exception:                             # noqa: BLE001
        return None
    was = "mock" if isinstance(data.get("ledger"), dict) else "devnet"
    if was != _MODE:
        print(f"  ℹ️ {LEGACY_PATH} 는 {was} 상태로 보입니다 — "
              f"현재 {_MODE} 기동이라 건드리지 않습니다.")
        return None
    LEGACY_PATH.replace(STATE_PATH)
    print(f"  ↻ 상태 파일 이관: {LEGACY_PATH} → {STATE_PATH} ({was})")
    return STATE_PATH


def load() -> dict:
    """저장된 상태를 되살린다. make_demo_bots() **뒤에** 호출해야 한다.

    데모봇은 이미 만들어져 있으므로 가변 상태(거래 카운터·킬 스위치)만
    덮어쓰고, 모르는 bot_id만 새로 만든다. 그래야 데모봇 정의는 코드가
    진실원으로 남고, 사용자 봇은 파일이 진실원이 된다.
    """
    from app.bots import BOTS, BotInstance
    from app.core.ledger import LEDGER
    from app.core.positions import BOOK, Position
    from app.core.receipts import RECEIPTS
    from app.models import DecisionReceipt, Rulebook

    out = {"bots": 0, "restored_bots": [], "receipts": 0, "positions": 0,
           "ledger": False}

    path = STATE_PATH
    if not path.exists():
        path = _migrate_legacy()
        if path is None:
            return out

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:                        # noqa: BLE001
        print(f"  ⚠️ 상태 파일을 읽을 수 없습니다({path}): {str(e)[:120]}")
        return out

    if data.get("version") != VERSION:
        print(f"  ⚠️ 상태 파일 버전 불일치 (파일 {data.get('version')} ≠ "
              f"코드 {VERSION}) — 복원을 건너뜁니다.")
        return out

    # 모드가 다르면 복원하지 않는다. 봇과 포지션만 살아나고 원장은 빈
    # 상태가 되어, 온체인(또는 메모리)에 실물이 없는 포지션이 생긴다.
    # 그 포지션은 청산이 안 되므로 자금이 영구히 묶인다.
    saved_mode = data.get("ledger_mode")
    if saved_mode is not None and saved_mode != _MODE:
        print(f"  ⚠️ 상태 파일이 다른 원장 모드에서 저장됐습니다 "
              f"(파일 {saved_mode!r} ≠ 현재 {_MODE!r}) — 복원을 건너뜁니다.\n"
              f"     {path} 를 그 모드로 기동할 때 쓰세요.")
        return out

    # 원장을 봇보다 먼저 되살린다. 순서가 반대면 봇 조회가 빈 원장을 본다.
    if data.get("ledger") and hasattr(LEDGER, "load_state"):
        LEDGER.load_state(data["ledger"])
        out["ledger"] = True

    # 원장이 지갑을 못 대는 봇은 되살리지 않는다.
    # 상태 파일은 LEDGER_MODE를 가리지 않고 쓰인다. mock으로 만든 봇을
    # devnet 프로세스가 되살리면 devnet 원장에는 그 키페어가 없어서
    # 첫 이체에서 KeyError로 죽는다. 조용히 반쪽 봇을 만드는 것보다
    # 건너뛰고 이유를 남기는 편이 낫다.
    def _servable(bot_id: str) -> bool:
        keys = getattr(LEDGER, "keypairs", None)
        if keys is None:              # mock 원장은 어떤 지갑이든 만들어 준다
            return True
        return f"user-treasury@{bot_id}" in keys

    skipped: set[str] = set()
    for b in data.get("bots", []):
        if not _servable(b["bot_id"]):
            skipped.add(b["bot_id"])
            print(f"  ⚠️ 복원 건너뜀({b['bot_id']}): 현재 원장에 이 봇의 지갑이 "
                  f"없습니다. 다른 LEDGER_MODE에서 만들어진 봇입니다.")
            continue
        bot = BOTS.get(b["bot_id"])
        if bot is None:
            bot = BotInstance(bot_id=b["bot_id"], owner=b["owner"],
                              rulebook=Rulebook(**b["rulebook"]),
                              created_at=b.get("created_at", 0.0))
            BOTS[b["bot_id"]] = bot
            out["restored_bots"].append(b["bot_id"])
        bot.trades_today = b.get("trades_today", 0)
        bot._trade_day = b.get("trade_day", bot._trade_day)
        bot.killed = b.get("killed", False)
        bot.tracker.policy.killed = bot.killed
        out["bots"] += 1

    for r in data.get("receipts", []):
        rec = DecisionReceipt(**r)
        RECEIPTS.receipts[rec.receipt_id] = rec
        out["receipts"] += 1
    RECEIPTS.anchors = list(data.get("anchors", []))

    for p in data.get("positions", []):
        # 영수증 없는 포지션은 정산할 수 없다(분배표가 영수증에 있다).
        # 되살려봐야 settle에서 죽으므로 경고하고 건너뛴다.
        if p["receipt_id"] not in RECEIPTS.receipts:
            print(f"  ⚠️ 영수증 없는 포지션 건너뜀: {p['receipt_id']}")
            continue
        if p["bot_id"] in skipped:      # 주인 없는 포지션은 청산할 주체가 없다
            continue
        BOOK.open(Position(**p))
        out["positions"] += 1

    return out
