"""Gemini mainnet 실결제 점검 — 진짜 돈이 나갑니다.

    py -3.13 check_gemini.py

Flash 1회, Pro 1회만 호출합니다. 총 $0.01 미만.
전체 사이클을 돌리기 전에 이걸로 경로부터 확인하세요.
"""
import os

os.environ["INFERENCE_MODE"] = "managed"
os.environ.setdefault("MAINNET_TOTAL_CAP", "50000")   # $0.05 — 점검용 최소 상한

from app.adapters import gemini_live
from app.core.spend_guard import GUARD

print("=" * 60)
print("⚠️  이 스크립트는 mainnet 실제 USDC를 사용합니다.")
print(f"   총액 상한: ${GUARD.stats()['cap_usd']:.4f}")
print("=" * 60)

HEADLINE = "Nvidia Corp — 차세대 추론칩 수요 예상치 상회 보고"

print("\n① Flash 스크리닝 (gemini-3.6-flash)")
try:
    score = gemini_live.screen(HEADLINE)
    print(f"   관련성 점수: {score}  ✅")
except Exception as e:
    print(f"   ❌ {str(e)[:200]}")

print(f"\n   누적 지출: ${GUARD.stats()['spent_usd']:.6f}")

print("\n② Pro 심층추론 (gemini-3.1-pro-preview)")
try:
    out = gemini_live.analyze("NVDA", HEADLINE)
    print(f"   방향   : {out['side']}")
    print(f"   확신도 : {out['confidence']}")
    print(f"   근거   : {out['rationale'][:80]}")
    print("   ✅")
except Exception as e:
    print(f"   ❌ {str(e)[:200]}")

s = GUARD.stats()
print("\n" + "=" * 60)
print(f"총 {s['calls']}회 호출 / ${s['spent_usd']:.6f} 지출 "
      f"(잔여 ${s['remaining_usd']:.6f})")
if s["calls"] >= 2:
    print("\n✅ 실결제 경로 확인. 전체 사이클을 돌리려면:")
    print('     $env:INFERENCE_MODE="managed"    # 추론만 진짜 Gemini')
    print('     $env:MAINNET_TOTAL_CAP="500000"  # $0.50 상한')
    print('     $env:LEDGER_MODE="devnet"        # 결제는 devnet USDC')
    print("     py -3.13 test_cycle.py")
    print('\n   ⚠️ X402_MODE 는 건드리지 마세요 (기본 mock).')
    print("      paysh 경로는 스키마 미확정이라 켜면 전 사이클이 멈춥니다.")
    print("      INFERENCE_MODE 와 X402_MODE 는 서로 다른 축입니다 —")
    print("      진짜 Gemini를 쓰는 데 필요한 것은 INFERENCE_MODE 하나뿐입니다.")
print("=" * 60)
