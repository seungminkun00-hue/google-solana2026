"""전역 설정 (Managed v2).

모든 금액은 마이크로 USDC 정수 (1 USDC = 1_000_000).
"""
import os

USDC = 1_000_000

# ── 실행 모드 ───────────────────────────────────────────────────────
# [2026-08] 축을 둘로 분리했다. 이전에는 INFERENCE_MODE 하나가 서로 다른
# 두 가지를 동시에 건드려서, managed로 켜면 실제 Gemini를 호출하는
# 라우트 자체를 안 타는 자기모순이 있었다. 상세는 app/inference.py 주석.
#
#   INFERENCE_MODE — 추론 백엔드를 고른다
#     mock    : 내장 모의 판단 (네트워크·비용 0, 데모 기본값)
#     managed : 실제 Gemini에 mainnet 결제 (app/adapters/gemini_live.py)
#
#   X402_MODE — 결제 경로를 고른다
#     mock    : 내장 모의 402 페이월 (app/external.py)
#     paysh   : 외부 pay.sh 게이트웨이 — 스키마 미확정이라 현재 비활성
INFERENCE_MODE = os.environ.get("INFERENCE_MODE", "mock")
X402_MODE = os.environ.get("X402_MODE", "mock")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "dev-token")

# ── 외부 API 가격표 ─────────────────────────────────────────────────
PRICE_EXA_SEARCH = 2_000        # $0.002
# [실측 교정 2026-07] recon.py로 pay.sh 실단가 확인 후 조정.
# 이전 추측(1_500 / 18_000)은 각각 10배 / 3배 과대평가였다.
# 과대평가하면 should_think가 과도하게 SKIP해 기회를 놓친다.
# 정확한 값은 app/pricing.py가 프롬프트 길이로 동적 계산한다.
PRICE_GEMINI_FLASH = 150        # gemini-3.6-flash        (실측 기반 평균)
PRICE_GEMINI_DEEP = 5_600       # gemini-3.1-pro-preview  (실측 기반 평균)
PRICE_MARKET_DATA = 4_000       # $0.004

# ── 판매 가격 ───────────────────────────────────────────────────────
PRICE_SIGNAL = 50_000           # $0.05
PRICE_THESIS = 200_000          # $0.20

# ── MVoT ────────────────────────────────────────────────────────────
MIN_ROI = 5
COLD_START_SAMPLES = 20         # 이 미만이면 보수적 기본값 사용
COLD_HIT_RATE = 0.5
COLD_AVG_RETURN = 0.02

# ── 인지비용 지출 정책 ──────────────────────────────────────────────
# ⚠️ mainnet 전환 시 이 숫자가 진짜 돈을 지킨다.
#    devnet에서는 실수해도 0원이었지만, 이제는 아니다.
#    환경변수로 덮어쓸 수 있게 해서, mainnet에서는 더 낮게 건다.
RESEARCH_DAILY_CAP = int(os.environ.get("RESEARCH_DAILY_CAP", 500_000))
RESEARCH_PER_DECISION_CAP = int(
    os.environ.get("RESEARCH_PER_DECISION_CAP", 100_000))

# mainnet 실결제 총액 상한. 프로세스 전체에 걸린 마지막 방어선.
# 넘으면 어떤 이유로도 더 결제하지 않는다.
MAINNET_TOTAL_CAP = int(os.environ.get("MAINNET_TOTAL_CAP", 1_000_000))  # $1.00

# ── 인지비용 만다트 (revenue → research) ────────────────────────────
COG_MANDATE_WEEKLY_CAP = 10 * USDC
COG_MANDATE_MIN_ROI = 5

# ── 매매자금 만다트 (user-treasury → invest) ────────────────────────
CAP_MANDATE_WEEKLY_CAP = 200 * USDC
CAP_MANDATE_MIN_HIT_RATE = 0.40     # 최근 적중률이 이 밑이면 추가투입 거절
CAP_MANDATE_MAX_EXPOSURE = 300 * USDC

# ── 정산 분배 비율 (bps) ────────────────────────────────────────────
SPLIT_USER_BPS = 8_500
SPLIT_REVENUE_BPS = 1_000
SPLIT_RESEARCH_BPS = 500
