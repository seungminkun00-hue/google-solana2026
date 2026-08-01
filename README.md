# Cognitive Economy v2 — Managed 자율결제 투자봇

Google Cloud × Solana AI Agentic Hackathon / Track A

**검증 결과: mock 23/23 · devnet 25/25 통과** → [VERIFICATION.md](VERIFICATION.md)
(재현: `py -3.13 verify_scenario.py`)

## 한 문장
사용자가 룰북(헌법)으로 봇을 만들면, 봇은 x402 페이월에서 뉴스·추론을
구매해 판단하고, 그 판단을 다른 봇에게 판매해 인지비용을 자급하며,
돈이 부족하면 스스로 청구서를 발행해 만다트 정책이 자동 승인·거절하고,
수익은 생성 시점에 박제된 비율로 원자적으로 분배된다.

## 트랙 3요건 → 코드 (이중 충족)
| 요건 | 만다트 1 (인지비용) | 만다트 2 (매매자금) |
|---|---|---|
| 결제요청 생성 | research가 인보이스 발행 | invest가 인보이스 발행 |
| 입금 | revenue→research 자동승인 | treasury→invest 자동승인 |
| 거절 조건 | ROI·주간한도·재원 | **성과하한 40%·노출한도** |

정산: 실현손익을 영수증에 박제된 비율(85/10/5)로 `transfer_many` 원자 분배.
devnet에서 수취인 3명이 **같은 트랜잭션 시그니처를 공유**하는 것으로 증명됨
([VERIFICATION.md](VERIFICATION.md) A3b).

## 30초 안내 — 어디부터 보면 되나

```powershell
py -3.13 -m pip install -r requirements.txt

py -3.13 verify_scenario.py   # ★ 여기부터. 25개 항목을 실행으로 증명 (15초)
py -3.13 audit.py             # 보안 감사 9항목 (1초)
py -3.13 test_cycle.py        # 봇 3개 사이클 로그를 눈으로 (1초)
```

주장이 어느 코드에 있는지:

| 주장 | 파일 · 함수 |
|---|---|
| 자기 생각값을 스스로 벌어 지불한다 | `core/routes.py` `ALLOWED_ROLE_ROUTES` (원금→인지비용 경로가 아예 없다)<br>`core/mandate.py` `CognitiveMandate.process()` |
| 신용카드가 아니라 한도 있는 법인카드 | `core/x402_client.py` `SpendTracker.check()` (결정당·일일)<br>`core/spend_guard.py` `SpendGuard` (mainnet 총액)<br>`core/mandate.py` 거절 3종 |
| 증명 가능성이 곧 판매 가능성 | `core/receipts.py` `effective_mode()` (폴백을 숨기지 않는다)<br>`main.py` `sell_thesis()` (`receipt_complete`가 거짓이면 판매 거부) |
| 정산의 원자성 + 멱등성 | `adapters/devnet_ledger.py` `transfer_many()` · `_send_tx()` |

## 구조

```
                사용자
                  │ POST /bots (룰북 + 예치금)
                  ▼
    ┌─────────────────────────────┐
    │  BotInstance (봇 하나)       │   지갑 4개가 역할별로 격리된다
    │                             │
    │  user-treasury   원금 보관   │◀────────┐ 정산 85%
    │       │ 자본 만다트(청구·승인)│         │
    │       ▼                     │         │
    │  invest-wallet   매매 자금   │─────────┤ 정산 10%
    │       │ swap_in/out         │         │
    │       ▼                     │         │
    │    [market] 미러 주식 토큰   │         │
    │                             │         │
    │  revenue-wallet  판매 수익   │◀────────┘ 정산 5%
    │       │ 인지 만다트(청구·승인)│
    │       ▼                     │
    │  research-agent  인지 예산   │  0에서 시작 — 인보이스로만 조달
    │       │ x402 결제            │
    └───────┼─────────────────────┘
            ▼
    뉴스 · Flash · Deep · 시세 · 다른 봇의 시그널
```

```
app/
  config.py        가격표·정책 상수·모드 스위치
  models.py        Rulebook·Invoice·DecisionReceipt·Thesis
  bots.py          BotInstance (봇별 지갑4·정책·만다트2) + 데모봇 3
  inference.py     결제 경로 라우팅 (X402_MODE)
  external.py      모의 402 세계 (Exa·Flash·Deep·시세)
  pricing.py       실측 단가 기반 토큰 비용 계산
  main.py          FastAPI 라우트 + 인증
  core/
    routes.py        역할 기반 경로 화이트리스트 ★ 원금 보호의 핵심
    ledger.py        mock 원장 + 통화량 불변식
    proofs.py        증빙 1회성 소비 (재사용·교차사용 차단)
    x402_client.py   paid_fetch (402→정책→결제→재요청, 실패시 롤백)
    x402_provider.py paywall (봇별 동적 수취)
    mandate.py       CognitiveMandate·CapitalMandate (주간 한도 롤오버)
    receipts.py      영수증·앵커·정산·stats + 추론 출처 정직성
    positions.py     포지션 장부 + 룰북 청산 판정
    scheduler.py     무인 순회 + 연속 실패 자동 정지
    spend_guard.py   mainnet 총액 상한 (마지막 방어선)
    store.py         재시작 생존 (봇·영수증·포지션 복원)
  adapters/
    devnet_ledger.py  실제 SPL 이체 — 원자적 정산 + 멱등 재전송
    gemini_live.py    Gemini mainnet 실결제 (pay CLI 경유)
    live_quotes.py    실시간 시세 (pay.sh MPP)
    universe.py       SEC 추적 종목 1,045개 검증
    paysh_client.py   MPP 402 챌린지 해독기 (실측 기록)
verify_scenario.py   전체 시나리오 검증 — A~E 25항목
```

## 실행

```powershell
py -3.13 -m uvicorn app.main:app --port 8100
# 브라우저 → http://127.0.0.1:8100/docs
# ① POST /demo/seed          (x-admin-token: dev-token)
# ② POST /bots/bot2/cycle    (x-admin-token: dev-token)
# ③ GET /bots  /  GET /bots/bot2/state   (인증 불필요)
```

**인증**: 자금이 움직이는 라우트(`/cycle` `/replenish` `/settle` `/close-all`
`/manage-positions`)는 `X-Admin-Token` 이 필요하다. 조회 라우트(`/bots`,
`/bots/{id}/state`, `/state`)와 판매 창구(`/sell/...`, x402 결제로 보호)는
그대로 열려 있다. 내부 파이프라인(스케줄러 등)은 프로세스마다 새로 만드는
1회용 내부 토큰으로 통과하므로 관리자 토큰을 들고 다니지 않는다.

## 모드 스위치 (시연 사고 방지)

세 축이 서로 독립이다. 하나를 바꿔도 나머지는 그대로다.

```powershell
$env:INFERENCE_MODE="mock"      # 추론: 모의 판단 (기본, 항상 작동)
$env:INFERENCE_MODE="managed"   # 추론: 실제 Gemini에 mainnet 결제

$env:LEDGER_MODE="mock"         # 원장: 메모리 (기본)
$env:LEDGER_MODE="devnet"       # 원장: 실제 SPL 이체

$env:PRICE_SOURCE="mock"        # 시세: 내장 기준가 (기본)
$env:PRICE_SOURCE="live"        # 시세: pay.sh MPP 라우트
```

`INFERENCE_MODE=managed` 는 결제 경로를 바꾸지 않는다. 내장 x402 페이월을
그대로 쓰면서 그 안의 추론만 진짜 Gemini로 바꾼다.

`X402_MODE=paysh` (결제 경로를 외부 pay.sh 게이트웨이로) 는 **아직 쓸 수
없다.** 켜면 명시적 예외로 막힌다 — 남은 작업 목록은 `app/inference.py`
주석에 있다. 예전에는 이 축이 `INFERENCE_MODE` 에 묶여 있어서
"진짜 Gemini를 켜면 진짜 Gemini를 안 부르는" 자기모순이 있었다.

## 두 네트워크를 쓰는 이유

```
mainnet : Gemini 추론 결제    ← 게이트웨이가 강제 (localnet/devnet 불가)
devnet  : 봇 지갑·정산·미러 주식 ← 우리가 통제. 원자성·멱등성·불변식을 증명
```

**원장 안의 모든 송금은 devnet 자체 민트 하나로 닫혀 있다.** mainnet 결제는
`pay` CLI가 별도 지갑으로 내며 원장을 거치지 않는다. 두 통화를 잇는 브리지는
없고, `config.PRICE_*` 를 실측 단가에 맞춰 조정해 devnet 경제가 mainnet
비용을 모사한다. 자세한 한계는 [VERIFICATION.md](VERIFICATION.md) 5절.

## v2에서 새로 된 것
- 멀티봇: 봇별 지갑·정책·킬스위치 격리 (bot1 정지가 bot2에 무영향)
- 룰북 2중 방어: 사전필터(뉴스값 2원에서 차단) + 체결 게이트
- 2단계 모델: Flash 스크리닝 → 통과분만 Deep
  (실측 단가 기준 $0.000158 vs $0.005674 — **36배 차이**)
- 만다트 2종: 성과 나쁘면 사용자 돈 추가투입이 실제로 거절됨
- hit_rate/avg_return이 상수가 아니라 정산 영수증 집계 (콜드스타트 보호)
- 봇 간 시그널 거래: bot2가 bot1 시그널을 devnet USDC로 실제 구매
- **정산 멱등성**: 재시도가 분배를 두 번 집행하던 결함을 찾아 수정 (실측 증명)
- **재시작 생존**: 봇·영수증·포지션이 프로세스를 넘어 복원
