"""실전 시세 연동 점검 — 코드에 붙이기 전에 이것부터 돌려보세요.

    py -3.13 check_quotes.py

pay CLI가 402 결제를 거쳐 실제로 시세를 받아오는지 확인합니다.
성공하면 PRICE_SOURCE=live 로 전체 사이클을 돌릴 수 있습니다.
"""
import os
import time

os.environ["PRICE_SOURCE"] = "live"

from app.adapters import live_quotes

TICKERS = ["NVDAx", "TSLAx", "SPYx", "AAPLx"]

print("=" * 58)
print("pay CLI 확인:", "✅ 있음" if live_quotes.available() else "❌ 없음")
if not live_quotes.available():
    print("\n  npm install -g @solana/pay  후 다시 실행하세요.")
    print("  (PRICE_SOURCE 환경변수는 이 스크립트가 자동 설정합니다)")
    raise SystemExit

print("\n실전 시세 조회 (각 호출마다 402 결제가 일어납니다)\n")
ok = 0
for t in TICKERS:
    started = time.time()
    try:
        px = live_quotes.spot_live(t)
        took = time.time() - started
        print(f"  {t:6s} ${px/1e6:>10,.2f}   ({took:.1f}초)  ✅")
        ok += 1
    except Exception as e:
        print(f"  {t:6s} {'실패':>12s}   {str(e)[:60]}  ❌")

print(f"\n  성공 {ok}/{len(TICKERS)}")
if ok == len(TICKERS):
    print("\n✅ 전 종목 조회 성공. 이제 실전 시세로 사이클을 돌릴 수 있습니다:")
    print('     $env:PRICE_SOURCE="live"')
    print('     $env:LEDGER_MODE="devnet"')
    print("     py -3.13 test_cycle.py")
else:
    print("\n⚠️ 일부 실패. 실패한 종목은 내장 기준가로 자동 폴백되므로")
    print("   사이클 자체는 계속 돌아갑니다.")
print("=" * 58)
