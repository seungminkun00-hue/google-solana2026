"""기록 계층 — 화면이 물어보는 질문에 답하려면 남겨야 하는 것들.

[왜 필요한가]
기존 시스템은 '지금 상태'는 완벽하게 안다. 잔고·열린 포지션·영수증이
전부 있다. 그런데 앱 화면이 묻는 것은 대부분 '지나온 것'이다.

    "거래 내역을 보여줘"        → 체결은 BOOK.open/close 로 사라진다
    "3개월 수익률 곡선"         → 시점별 자산 총액을 아무도 안 남긴다
    "이 봇이 어떤 API에 얼마 썼나" → 영수증에는 합계만 있고 내역이 없다

셋 다 계산으로 복원할 수 없다. 사후에 만들어내면 그건 지어낸 숫자다.
그래서 일어나는 순간에 적는다. 이 파일이 하는 일은 그것뿐이다.

[설계 원칙]
  · 기존 경로를 바꾸지 않는다. 부르는 쪽에서 한 줄 추가할 뿐이고,
    이 모듈이 예외를 던져 매매를 망가뜨리는 일은 없어야 한다.
  · 메모리 무한 증가를 막는다. 각 로그에 상한을 둔다.
  · store.py 가 통째로 저장·복원한다. 재시작해도 내역이 남는다.
"""
from __future__ import annotations

import threading
import time
import uuid

MAX_FILLS = 5_000
MAX_EQUITY = 20_000
MAX_API_CALLS = 20_000

# 어떤 URL이 어느 공급자인가. paid_fetch 가 넘겨주는 resource 문자열을
# 사람이 읽는 이름으로 바꾼다. 화면의 'API 탭'이 이 표를 그대로 쓴다.
#
# tags 는 디자인의 배지와 1:1 이고, paysh 는 그 호출이 x402 페이월을
# 통과했는지를 뜻한다. 내장 페이월도 x402 규약을 그대로 따르므로 True다.
PROVIDERS: dict[str, dict] = {
    "/mock/exa/search": {
        "key": "exa",
        "name": "Exa",
        "tags": ["뉴스", "시장 분석"],
        "paysh": True,
        "kind": "external",
    },
    # [2026-08-02] 고정 모델명을 쓰지 않는다. 어느 모델이 실제로 답했는지는
    # 호출마다 다르고(BYOK 모델 선택·폴백), 그 값은 각 호출 행의 model 에
    # 남는다. 여기 이름은 '이 라우트가 무슨 일을 하는가' 만 말한다.
    "/mock/gemini/flash": {
        "key": "gemini-flash",
        "name": "Gemini — 1차 스크리닝",
        "tags": ["AI 모델", "1차 스크리닝"],
        "paysh": True,
        "kind": "inference",
    },
    "/mock/gemini/deep": {
        "key": "gemini-pro",
        "name": "Gemini — 심층 추론",
        "tags": ["AI 모델", "투자 판단"],
        "paysh": True,
        "kind": "inference",
    },
    "/mock/market/quote": {
        "key": "market-data",
        "name": "Market Data",
        "tags": ["시세", "시장 분석"],
        "paysh": True,
        "kind": "external",
    },
}


def classify(resource: str) -> dict:
    """resource 문자열 → 공급자 정보.

    봇끼리 사고파는 라우트는 /bots/{id}/sell/{종류}/{id} 라 고정 표에
    넣을 수 없다. 경로 모양으로 판별한다.
    """
    hit = PROVIDERS.get(resource)
    if hit:
        return hit
    parts = resource.strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "bots" and parts[2] == "sell":
        what = "시그널" if parts[3] == "signal" else "투자 판단"
        return {"key": f"bot-{parts[3]}", "name": f"봇 {what} 구매",
                "tags": ["봇 간 거래", what], "paysh": True, "kind": "peer"}
    return {"key": "other", "name": resource or "기타",
            "tags": ["기타"], "paysh": False, "kind": "other"}


class Journal:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.fills: list[dict] = []
        self.equity: list[dict] = []
        self.api_calls: list[dict] = []

    # ── 체결 ────────────────────────────────────────────────────
    def record_fill(self, *, bot_id: str, ticker: str, side: str, qty: int,
                    price_micro: int, gross_micro: int, receipt_id: str,
                    tx: str = "", pnl_micro: int | None = None,
                    reason: str = "", hold_hours: float | None = None) -> dict:
        """매수·매도 한 건. qty·price·gross 는 전부 마이크로 단위 정수다.

        side 는 "buy" | "sell". 매도일 때만 pnl_micro·reason(청산 사유)·
        hold_hours 가 채워진다 — 셋 다 청산 시점에만 존재하는 사실이고,
        나중에 계산으로 복원할 수 없다(포지션이 장부에서 사라지므로).
        """
        row = {
            "fill_id": f"fill_{uuid.uuid4().hex[:12]}",
            "bot_id": bot_id, "ticker": ticker, "side": side,
            "qty": qty, "price_micro": price_micro, "gross_micro": gross_micro,
            "receipt_id": receipt_id, "tx": tx,
            "pnl_micro": pnl_micro, "reason": reason,
            "hold_hours": hold_hours,
            "ts": time.time(),
        }
        with self._lock:
            self.fills.append(row)
            del self.fills[:-MAX_FILLS]

        # 앱 상단 팝업 알림. 체결이 일어나는 지점은 여기 하나뿐이라
        # (매수는 /cycle, 매도는 /settle 이 둘 다 이 함수를 부른다)
        # 알림도 여기서 한 번만 내보내면 빠지거나 겹치지 않는다.
        from app.core.events import emit_fill
        emit_fill(bot_id=bot_id, ticker=ticker, side=side, qty=qty,
                  price_micro=price_micro, gross_micro=gross_micro,
                  pnl_micro=pnl_micro, reason=reason, tx=tx)
        return row

    def fills_of(self, bot_id: str) -> list[dict]:
        with self._lock:
            return [f for f in self.fills if f["bot_id"] == bot_id]

    # ── 자산 추이 ───────────────────────────────────────────────
    def record_equity(self, bot_id: str, total_micro: int,
                      cash_micro: int, market_micro: int) -> None:
        """이 시점 이 봇의 총 평가액. 수익률 곡선의 유일한 재료다.

        같은 초에 두 번 들어오면 뒤엣것으로 덮는다. 사이클 한 번에
        여러 번 불릴 수 있는데 그때마다 점을 찍으면 곡선이 톱니가 된다.
        """
        row = {"bot_id": bot_id, "ts": time.time(), "total": total_micro,
               "cash": cash_micro, "market": market_micro}
        with self._lock:
            last = None
            for e in reversed(self.equity):
                if e["bot_id"] == bot_id:
                    last = e
                    break
            if last is not None and int(last["ts"]) == int(row["ts"]):
                last.update(row)
                return
            self.equity.append(row)
            del self.equity[:-MAX_EQUITY]

    def equity_of(self, bot_id: str, since: float | None = None) -> list[dict]:
        with self._lock:
            out = [e for e in self.equity if e["bot_id"] == bot_id]
        if since is not None:
            out = [e for e in out if e["ts"] >= since]
        return out

    # ── API 호출 ────────────────────────────────────────────────
    def record_api_call(self, bot_id: str, resource: str, amount: int,
                        tx: str = "", model: str = "") -> None:
        """x402 결제 한 건. paid_fetch 가 결제에 성공한 직후 부른다.

        tx 는 그 결제의 온체인 서명이다(devnet 모드). 이게 없으면 화면이
        "API를 호출당 결제했다"고 말만 하고 증거를 못 댄다 — 심사위원이
        Explorer 에서 직접 확인할 수 있어야 주장이 성립한다.

        model 은 그 호출에 실제로 답한 추론 모델 ID. 선언한 모델이 아니라
        답한 모델을 적는다(폴백이 일어나면 비어 있거나 mock 이 된다).
        """
        info = classify(resource)
        row = {"bot_id": bot_id, "resource": resource, "provider": info["key"],
               "amount": amount, "tx": tx, "model": model, "ts": time.time()}
        with self._lock:
            self.api_calls.append(row)
            del self.api_calls[:-MAX_API_CALLS]

    def api_calls_of(self, bot_id: str) -> list[dict]:
        with self._lock:
            return [c for c in self.api_calls if c["bot_id"] == bot_id]

    # ── 저장·복원 ───────────────────────────────────────────────
    def dump(self) -> dict:
        with self._lock:
            return {"fills": list(self.fills), "equity": list(self.equity),
                    "api_calls": list(self.api_calls)}

    def load(self, data: dict) -> None:
        with self._lock:
            self.fills = list(data.get("fills", []))[-MAX_FILLS:]
            self.equity = list(data.get("equity", []))[-MAX_EQUITY:]
            self.api_calls = list(data.get("api_calls", []))[-MAX_API_CALLS:]


JOURNAL = Journal()
