"""토큰 기반 동적 가격 계산 (recon.py 실측 기반).

[왜 필요한가]
config.py의 고정 가격은 우리 '추측'이었다. 실측 결과 3배 과대평가였고,
과대평가하면 should_think가 필요 이상으로 SKIP해서 좋은 기회를 놓친다.

[실측 단가] 2026-07, pay skills show solana-foundation/google/generativelanguage
    gemini-3.6-flash        입력 $0.345/1M   출력 $2.875/1M
    gemini-3.1-pro-preview  입력 $2.30/1M    출력 $13.80/1M

[한계] 출력 토큰은 미리 알 수 없다. 보수적으로 상한을 잡는다.
       실제 402(x402/upto 스킴)도 '최대 얼마까지'를 먼저 잡고
       실사용분만 정산하는 방식이라 같은 발상이다.
"""
from __future__ import annotations

# 100만 토큰당 마이크로USDC ($1 = 1_000_000)
MODEL_RATES = {
    "gemini-3.6-flash":       {"input": 345_000,   "output": 2_875_000},
    "gemini-3.1-pro-preview": {"input": 2_300_000, "output": 13_800_000},
}

FLASH = "gemini-3.6-flash"
PRO = "gemini-3.1-pro-preview"


def estimate_tokens(text: str) -> int:
    """대략적 토큰 수. 영어 ~4글자, 한글 ~1.5글자가 1토큰.

    정확할 필요는 없다. should_think는 '살지 말지'만 정하면 되고,
    실제 청구는 402가 알려주는 값으로 이뤄진다.
    """
    if not text:
        return 0
    hangul = sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
    other = len(text) - hangul
    return max(1, int(hangul / 1.5 + other / 4))


def estimate_cost(model: str, prompt: str, max_output_tokens: int = 400) -> int:
    """이 호출에 들 최대 비용(마이크로USDC) 추정.

    max_output_tokens를 상한으로 잡으므로 실제보다 비싸게 나온다 =
    보수적이다. 예산을 초과할 일은 없다.
    """
    rate = MODEL_RATES.get(model)
    if rate is None:
        raise ValueError(f"단가 미등록 모델: {model}")
    tin = estimate_tokens(prompt)
    cost_in = tin * rate["input"] // 1_000_000
    cost_out = max_output_tokens * rate["output"] // 1_000_000
    return max(1, cost_in + cost_out)


def flash_cost(prompt: str) -> int:
    """1차 스크리닝. 출력이 점수 하나뿐이라 짧게 잡는다."""
    return estimate_cost(FLASH, prompt, max_output_tokens=50)


def deep_cost(prompt: str) -> int:
    """심층 추론. 근거 문장까지 나오므로 넉넉히."""
    return estimate_cost(PRO, prompt, max_output_tokens=400)


if __name__ == "__main__":
    sample = "NVIDIA, 차세대 추론칩 수요 예상치 상회 보고. 데이터센터 매출 전년 대비 62% 증가."
    print(f"샘플 헤드라인 ({len(sample)}자 ≈ {estimate_tokens(sample)}토큰)\n")
    f, d = flash_cost(sample), deep_cost(sample)
    print(f"  Flash 스크리닝  {f:>8,} µUSDC  (${f/1e6:.6f})")
    print(f"  Pro 심층추론    {d:>8,} µUSDC  (${d/1e6:.6f})")
    print(f"\n  기존 config 추측값")
    print(f"  PRICE_GEMINI_FLASH = 1,500   → 실측 {f:,}")
    print(f"  PRICE_GEMINI_DEEP  = 18,000  → 실측 {d:,}")
    print(f"\n  2단계 필터 효과 (뉴스 100건, 5건만 통과)")
    all_deep = 100 * d
    tiered = 100 * f + 5 * d
    print(f"    전부 Pro : {all_deep:>9,} µUSDC")
    print(f"    Flash선별: {tiered:>9,} µUSDC  ({100 - tiered*100//all_deep}% 절감)")
