"""종목 유니버스 점검 — smartmoney.market 무료 라우트.

    py -3.13 check_universe.py

돈이 들지 않습니다. 무료 라우트만 씁니다.
"""
from app.adapters import universe
from app.bots import BOTS, make_demo_bots, validate_rulebook

print("=" * 58)
d = universe.load(force=True)
print(f"출처: {d['source']}")
print(f"  SEC 내부자 추적 종목  {d['stock_count']:,}개")
print(f"  13-F 공시 운용사      {d['fund_count']}개")
print(f"  캐시 → {universe.CACHE_PATH}")

print("\n봇별 룰북 검증")
make_demo_bots()
for b in BOTS.values():
    bad = validate_rulebook(b.rulebook)
    mark = "❌ " + str(bad) if bad else "✅"
    names = ", ".join(universe.company_name(t) for t in sorted(b.rulebook.allowed_tickers))
    print(f"  {b.bot_id} {b.rulebook.label:12s} {mark}")
    print(f"       {names}")

print("\n유명 운용사 샘플")
print("  " + ", ".join(d["funds"][:12]))
print("=" * 58)
