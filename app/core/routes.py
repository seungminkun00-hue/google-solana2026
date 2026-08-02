"""지갑 경로 규칙과 원장 공용 예외.

[왜 별도 파일인가 — 2026-08]
예전에는 이 내용이 app/core/ledger.py 안에 있었고, devnet_ledger.py가
거기서 임포트했다. 그런데 ledger.py 맨 아래 `LEDGER = _make()` 가
LEDGER_MODE=devnet 이면 devnet_ledger.py를 임포트한다 — 순환이다.

    app.core.ledger  ──_make()──▶  app.adapters.devnet_ledger
           ▲                                  │
           └────── ALLOWED_ROLE_ROUTES ───────┘

app.core.ledger 를 '먼저' 임포트할 때만 우연히 동작했다. 순서가 반대면
ImportError로 죽는다. 진단 스크립트를 쓸 때마다 밟는 지뢰였다.

공유하는 것만 여기로 내리면 양쪽 다 이 파일을 임포트하고, 서로는
임포트하지 않는다. 순환이 구조적으로 사라진다.
(app.core.ledger 는 하위 호환을 위해 이 이름들을 재수출한다.)
"""
from __future__ import annotations

# 지갑 이름 규칙:  "역할@봇ID"   예) research-agent@bot1
# 전역 지갑:       "external", "market"
#
# 경로 검사는 '역할' 기준이다. 그래서 봇 간 거래
# (A봇 research → B봇 revenue = 시그널 구매)가 자연스럽게 허용되고,
# 사용자 원금 유출 같은 금지 경로는 봇이 몇 개든 전부 차단된다.
ALLOWED_ROLE_ROUTES: set[tuple[str, str]] = {
    ("user-treasury", "invest-wallet"),    # 매매자금 위임/청구 승인
    ("revenue-wallet", "research-agent"),  # 인지비용 보충
    ("invest-wallet", "user-treasury"),    # 정산: 사용자 몫
    ("invest-wallet", "revenue-wallet"),   # 정산: 판매수익 지갑 몫
    ("invest-wallet", "research-agent"),   # 정산: 인지비용 보전
    ("research-agent", "external"),        # 외부 API 지출
    ("research-agent", "revenue-wallet"),  # 시그널/테제 구매 (봇 간 포함)
    ("external", "revenue-wallet"),        # 외부 에이전트의 구매 대금
    ("external", "user-treasury"),         # 사용자 예치 (온램프)
    ("external", "market"),                # 모의 유동성
    ("invest-wallet", "market"),           # 스왑 원금 유출
    ("market", "invest-wallet"),           # 청산 대금 유입
    # ── 심사위원 시연 경로 (2026-08) ──────────────────────────────
    # judge-wallet 은 **우리가 개인키를 갖지 않는** 외부 지갑이다.
    # 여기서 나가는 이체는 심사위원이 위임(approve)한 한도 안에서만,
    # delegated_transfer() 로만 가능하다.
    ("external", "judge-wallet"),          # 시연용 테스트 USDC 지급
    ("judge-wallet", "user-treasury"),     # 심사위원 예치 (위임 인출)
    # 정산 회수 — 심사위원이 넣은 돈이 **자기 지갑으로** 돌아오는 경로.
    #
    # ⚠️ ("user-treasury", "external") 은 여전히 금지다. 그게 audit.py 6번이
    #    검증하는 '사용자 원금 유출 차단'이고, 그 성질은 그대로 남는다.
    #    다른 점은 목적지가 임의의 외부가 아니라 **그 돈을 넣은 본인 지갑**
    #    이라는 것이다. 넣은 사람에게 돌려주는 것은 유출이 아니라 출금이고,
    #    이게 없으면 "매도해서 정산했다"를 화면 밖에서 확인할 방법이 없다.
    ("user-treasury", "judge-wallet"),     # 매도 정산 후 심사위원에게 반환
}


def role(wallet: str) -> str:
    return wallet.split("@")[0]


class RouteViolation(Exception):
    pass


class InsufficientFunds(Exception):
    pass
