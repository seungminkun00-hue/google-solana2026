"""x402 결제 경로 라우팅.

[2026-08 수정 — 자기모순 제거]
이전에는 INFERENCE_MODE 하나가 성격이 다른 두 축을 동시에 건드렸다.

    INFERENCE_MODE=managed
      ① route()가 https://pay.sh/... 를 반환    → 결제 경로가 바뀜
      ② gemini_live.enabled()가 True            → 추론 백엔드가 바뀜

그런데 gemini_live는 /mock/gemini/* 라우트 **안에서** 호출된다
(app/external.py의 gemini_flash·gemini_deep). managed로 켜는 순간
①이 그 라우트를 우회해버리므로 ②는 영원히 실행되지 않는다.
"진짜 Gemini를 쓰겠다"고 켜면 켤수록 진짜 Gemini에서 멀어지는
스위치였고, 게다가 ①이 가리키는 주소는 실측된 적이 없어서
전 사이클이 그 자리에서 무너졌다.

이제 두 축을 분리한다.

    INFERENCE_MODE  추론 백엔드 (모의 판단 ↔ 진짜 Gemini)
    X402_MODE       결제 경로   (내장 페이월 ↔ pay.sh 게이트웨이)

INFERENCE_MODE=managed는 내장 페이월 경로를 그대로 쓰면서 그 안의
추론만 진짜 Gemini로 바꾼다. 이것이 원래 의도한 동작이다.
결제는 devnet USDC로 이뤄지고 추론만 mainnet에서 산다 —
check_gemini.py가 검증하는 것도 정확히 이 경로다.
"""
from __future__ import annotations

from app import config

_MOCK_ROUTES = {
    "exa_search":   "/mock/exa/search",
    "gemini_flash": "/mock/gemini/flash",
    "gemini_deep":  "/mock/gemini/deep",
    "market_quote": "/mock/market/quote",
}

# pay.sh 게이트웨이 후보 주소. **아직 아무것도 실측되지 않았다.**
# 남겨두는 이유는 나중에 확정할 때의 출발점이기 때문이고,
# 지금 켜면 안 되는 이유는 아래 _PAYSH_BLOCKERS에 적어둔다.
_PAYSH_ROUTES = {
    "exa_search":   "https://pay.sh/api/exa/search",
    "gemini_flash": "https://pay.sh/api/google/gemini/flash",
    "gemini_deep":  "https://pay.sh/api/google/gemini/generate",
    "market_quote": "https://pay.sh/api/blocksize/quote",
}

# RECON.md 실측으로 확인된, pay.sh 경로를 켜기 전에 반드시 해결해야 할 것들.
# 하나라도 남아 있으면 paid_fetch는 402조차 못 받고 죽는다.
_PAYSH_BLOCKERS = """\
  ① 메서드: 라우트마다 다르다. debugger.pay.sh 시세 라우트는 GET만 받고
     POST는 403이었다(RECON.md). 반면 Gemini 게이트웨이는 POST가 정상이다
     (2026-08 재실측). paid_fetch는 POST 고정이라 라우트별 지정이 필요하다.
  ② 챌린지 위치: 가격이 본문이 아니라 www-authenticate 헤더에 base64로 있다.
     통역기 app/adapters/paysh_client.py:parse_mpp_challenge 가 이미 있지만
     paid_fetch가 아직 그걸 쓰지 않는다.
  ③ 위 _PAYSH_ROUTES 주소는 추측이다. recon.py로 실제 402를 받아 확정할 것."""


def route(key: str, base: str) -> str:
    """이 리소스를 어디서 사서 올지 결정한다.

    INFERENCE_MODE는 여기 관여하지 않는다. 그게 이 파일 수정의 핵심이다.
    """
    if config.X402_MODE == "paysh":
        raise NotImplementedError(
            "X402_MODE=paysh 는 아직 쓸 수 없습니다. 남은 작업:\n"
            + _PAYSH_BLOCKERS
            + "\n\n실제 Gemini 추론만 쓰고 싶은 것이라면 이 스위치가 아니라"
              " INFERENCE_MODE=managed 를 쓰세요."
              " 결제는 내장 페이월(devnet USDC), 추론만 mainnet이 됩니다.")
    return f"{base}{_MOCK_ROUTES[key]}"
