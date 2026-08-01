# 전체 시나리오 검증 보고서

재현: `py -3.13 verify_scenario.py` (LEDGER_MODE=mock 15초 / devnet 217초)

---

## 1. 한 줄 결론

**시나리오대로 동작한다.** 트랙 3요건(결제요청 생성·입금·정산)이 사람 개입 없이
한 사이클 안에서 전부 일어나고, devnet에서는 온체인 트랜잭션으로 증명된다.
mock 23/23, devnet **25/25** 통과. 실패 0건.

다만 아래 5절의 항목들은 아직 모의값이며, 발표에서 먼저 밝혀야 한다.

---

## 2. 트랙 요건 3가지 — 실제 로그

전부 **단일 `POST /bots/{id}/cycle` 호출 한 번** 안에서 일어난 것이다.
사람이 중간에 입력한 지점은 없다.

```
한 번의 POST /bots/bot_fe8e3b0c/cycle 로그 순서:
  ['budget-check', 'scout', 'analyst', 'external-sale', 'replenish',
   'rulebook', 'capital-invoice', 'executor', 'position-open']
```

### 요건 ① 결제 요청 생성 — 에이전트가 스스로 청구서를 발행한다

두 축이 각각 자기 부족액을 계산해 인보이스를 만든다.

```
[A1a] research-agent가 스스로 인보이스 발행
  budget-check → {"status": "approved", "amount": 300000,
                  "decided_reason": "인지 만다트 충족 — 부분(300000/500000) 자동 승인"}

[A1b] invest-wallet이 스스로 자본 청구
  capital-invoice → {"status": "approved", "amount": 16200000,
                     "decided_reason": "자본 만다트 충족 (적중률 50%) — 자동 승인"}
```

`amount`는 코드에 박힌 상수가 아니라 `issue_invoice()`가
`needed - balance`로 계산한 값이다. 그래서 매 실행마다 다르다.

### 요건 ② 입금 — 만다트가 사람 없이 승인한다. **그리고 거절한다.**

승인만 되면 그건 만다트가 아니라 자동출금이다. 거절 3종을 실제로 발동시켰다.

```
[A2b] 만다트 자동 거절
  성과 하한   → rejected: 성과 하한 미달: 적중률 30% < 40% — 추가 투입 거절
  노출 한도   → rejected: 총 노출 한도 초과
  재원/주간한도 → rejected: 지급 불가 (주간잔여 9450000, 재원 0)
```

노출 한도는 `BOOK.exposure()`(열린 포지션 원가 합)를 실제로 채워 발동시킨 것이고,
재원 부족은 판매수익 지갑에 없는 돈을 청구해 발동시킨 것이다.

### 요건 ③ 정산 — 사전 확정 비율로, 단일 트랜잭션으로

```
[A3a] 실현이익이 사전 확정 비율(85/10/5)로 분배
  기준가 131.00 → 150.65
  realized_pnl=2848915
  distribution=[
    {"to": "user-treasury@bot_fe8e3b0c",  "amount": 2421577},
    {"to": "revenue-wallet@bot_fe8e3b0c", "amount":  284891},
    {"to": "research-agent@bot_fe8e3b0c", "amount":  142447}]
  실제 비율={'user-treasury': 0.85, 'revenue-wallet': 0.1, 'research-agent': 0.05}

[A3b] 분배 3건이 단일 트랜잭션
  수취 3건이 공유하는 시그니처:
  5jH3hTaxVYJhwED1ThqNZhVERqf6TFKKvJumHgiYWJGcG2SoaTM6kA2BbnN4jFYg9fhszdbREj57iiD7KXxtSpsc
  https://explorer.solana.com/tx/5jH3hTaxVYJhwED1ThqNZhVERqf6TFKKvJumHgiYWJGcG2SoaTM6kA2BbnN4jFYg9fhszdbREj57iiD7KXxtSpsc?cluster=devnet

[A3c] 손실이면 분배가 비어 있다
  realized_pnl=-2474719  distribution=[]
```

**분배 비율은 영수증 생성 시점에 박제된 값**(`splits_bps`)이고, 정산 시점에
다시 계산하지 않는다. 세 수취인이 같은 시그니처를 공유한다는 것이
단일 트랜잭션의 증거다.

### 핵심 주장 — "에이전트가 자기 생각값을 스스로 벌어 지불한다"

```
[B1a] research-agent가 0에서 시작
  생성 직후: {"user-treasury": 200.0, "invest-wallet": 0.0,
              "research-agent": 0.0, "revenue-wallet": 0.3}

[B1b] research-agent 자금원이 인보이스뿐
  입금 3건의 출처: {'revenue-wallet', 'invest-wallet'}
  자원: ['invoice:inv_abf72c7e1e5e', 'invoice:inv_d8b4a6ad2237', 'settle:rcpt_0224af0628a6']
        (revenue-wallet=인보이스, invest-wallet=정산 분배분)

[B2] 사용자 원금 → 인지비용 경로 차단
  차단됨 (API 비용 직접 지불): 금지된 경로: user-treasury@… → external
  차단됨 (인지비용 원금 조달): 금지된 경로: user-treasury@… → research-agent@…

[B3] 판매 수익이 인지비용을 충당
  판매·정산으로 번 돈   684,891 µUSDC
  인지비용 총지출       123,500 µUSDC
  revenue-wallet 300000 → 534891
```

사용자 원금에서 인지비용이 나가는 경로는 **화이트리스트에 없어서** 예외로 막힌다.
시도해서 막히는 것을 확인했다.

---

## 3. 항목별 결과

| # | 항목 | mock | devnet |
|---|---|---|---|
| **A1a** | research-agent가 스스로 인보이스 발행 | ✅ | ✅ |
| **A1b** | invest-wallet이 스스로 자본 청구 | ✅ | ✅ |
| **A1c** | 사람 개입 지점 없음 | ✅ | ✅ |
| **A2a** | 만다트 자동 승인 (입금) | ✅ | ✅ |
| **A2b** | 만다트 자동 거절 3종 | ✅ | ✅ |
| **A3a** | 이익이 85/10/5로 분배 | ✅ | ✅ |
| **A3b** | 분배가 단일 트랜잭션 | ⏭️ 증명 불가 | ✅ 시그니처 확인 |
| **A3c** | 손실이면 분배 없음 | ✅ | ✅ |
| **B1a** | research-agent 0에서 시작 | ✅ | ✅ |
| **B1b** | 자금원이 인보이스뿐 | ✅ | ✅ |
| **B2** | 원금 → 인지비용 경로 차단 | ✅ | ✅ |
| **B3** | 판매 수익이 인지비용 충당 | ✅ | ✅ |
| **C1** | 룰북·예치금으로 봇 생성 | ✅ | ✅ |
| **C2** | 존재하지 않는 종목 거부 | ✅ | ✅ |
| **C3** | 스케줄러 무인 순회 | ✅ | ✅ |
| **C4a** | 룰북대로 매수 | ✅ | ✅ |
| **C4b** | 확신도 미달이면 매수 안 함 | ✅ | ✅ |
| **C4c** | 손절이 실제로 발동 | ✅ | ✅ |
| **C5** | 재시작 후 봇·포지션 생존 | ✅ 3건 복원 | ✅ 3건 복원 |
| **D1** | audit.py 전 항목 | ✅ 9/9 | ✅ 9/9 |
| **D2** | 봇 격리 | ✅ | ✅ |
| **D3** | 연속 실패 자동 정지 | ✅ | ✅ |
| **D4** | 킬 스위치 | ✅ | ✅ |
| **D5** | 정산 재시도 이중 집행 방지 | ⏭️ 해당 없음 | ✅ |
| **D6** | 통화량 보존 | ✅ | ✅ |
| | **합계** | **23/23** | **25/25** |

### E. 두 모드 등가성

같은 스크립트로 같은 결과가 나온다. 다른 지점은 **두 곳뿐**이고, 둘 다
"mock에는 그 개념이 없어서"다.

| 항목 | 차이 | 이유 |
|---|---|---|
| A3b 단일 트랜잭션 | devnet만 증명 | mock은 시그니처가 없다. 단일 락 안의 딕셔너리 연산이라 원자성은 구조적으로 성립하지만 **온체인 증거는 없다** |
| D5 재전송 멱등성 | devnet만 증명 | mock은 네트워크·재시도가 없다 |

그 외 24개 항목은 두 모드에서 동일하게 통과한다. 잔고 수치가 다른 것은
시세 난수와 devnet 상태 누적 때문이며, 판정에는 영향이 없다.

**증거로 확인한 devnet 통화량 보존:**
```
{"expected": 403170340000, "actual": 403170340000, "conserved": true}
```

---

## 4. 실패한 것

**최종 실행 기준 실패 0건.** 다만 검증을 만드는 과정에서 **두 번 실패했고**,
둘 다 검증 스크립트의 결함이었다. 기록해 둔다.

### ① C5가 공허하게 통과하고 있었다 (수정함)

처음 구성에서는 D3(연속 실패 자동 정지)이 시나리오 봇의 재원을 비운 뒤
C5(재시작 생존)를 돌렸다. 재원이 없으니 매수가 안 되고, 포지션 0건 상태로
`0건 → 0건, 일치=True`가 되어 **통과처럼 보였다.**

```
(수정 전) 포지션 0건 → 0건, 일치=True     ← 아무것도 검증하지 않음
(수정 후) 포지션 3건 → 3건, 일치=True     ← 실제 복원 확인
```

D3에 전용 봇을 따로 만들도록 분리하고, C5는 포지션이 0이면 통과로 치지 않게
바꿨다. **통과할 수 있는 검사보다, 공허하게 통과하는 검사가 더 나쁘다.**

### ② 자식 프로세스가 `app.main`을 임포트하지 않았다 (수정함)

재시작 검증용 자식 프로세스가 `app.bots`만 임포트해서 `STORE.load()`가
아예 호출되지 않았다. 복원 기능은 정상인데 검증이 "복원 실패"로 오판했다.

### ③ 검증 중 새로 발견한 실제 결함 — 킬 스위치의 구멍

devnet D2 로그에서 드러났다. **정지된 봇도 사이클을 직접 호출하면
인지비용 조달(만다트 입금)이 한 번 실행된다.**

```
bot1(정지) 로그: [{"step": "budget-check", "status": "approved", "amount": 10000,
                   "decided_reason": "인지 만다트 충족 — 전액 자동 승인"}, {"step": "scout", "blocked": …
```

원인: [`app/main.py`](app/main.py)의 `bot_cycle` 이 맨 먼저 `/replenish`를
호출하는데, `/replenish`에는 `bot.killed` 검사가 없다. 외부 지출은
`SpendTracker.check()`가 `scout` 단계에서 막지만(`blocked`), 그 앞의
내부 이체(revenue → research)는 이미 일어난 뒤다.

- **영향**: 낮음. 스케줄러는 `if bot.killed: continue`로 건너뛰므로,
  누군가 정지된 봇에 직접 `POST /cycle`을 해야 재현된다. 자금이 봇 밖으로
  나가지도 않는다(같은 봇의 지갑 간 이동).
- **고치려면**: `bot_cycle` 진입부나 `replenish`에 `if bot.killed: raise`를
  추가하면 된다. 한 줄이다.
- **이번 보고서에서는 고치지 않았다.** 검증 단계에서 코드를 바꾸면
  "검증한 것"과 "제출하는 것"이 달라지기 때문이다.

---

## 5. 아직 mock인 것 — 발표에서 먼저 밝힐 목록

### ① 뉴스 (Exa) — 헤드라인 4건 하드코딩

- **왜**: pay.sh Exa 라우트의 402 스키마가 확정되지 않았다. 실측한 것은
  `debugger.pay.sh` 시세 라우트뿐이다.
- **어디**: [`app/external.py`](app/external.py) `_NEWS` (4건 고정),
  `exa_search()` 라우트
- **전환**: [`app/inference.py`](app/inference.py) `_PAYSH_ROUTES["exa_search"]`
  주소 확정 → `X402_MODE=paysh`. 단, 같은 파일 `_PAYSH_BLOCKERS`의 3가지
  (GET 메서드, `www-authenticate` 헤더 챌린지, 주소 확정)를 먼저 해결해야 한다.
- 티커와 회사명은 실제다 — smartmoney.market SEC 추적 목록 1,045종목과 대조한다.

### ② 추론 (Gemini) — 기본이 모의 판단

- **왜**: mainnet 미충전. 게이트웨이는 mainnet 결제만 받는다(localnet 불가).
- **어디**: [`app/external.py`](app/external.py) `gemini_flash`/`gemini_deep`의
  폴백 분기. 실제 호출 경로는 [`app/adapters/gemini_live.py`](app/adapters/gemini_live.py)에 있다.
- **전환**: `pay topup` → `py -3.13 check_gemini.py` → `INFERENCE_MODE=managed`
- **⚠️ 실제 Gemini 응답을 받아본 적이 한 번도 없다.** 라우팅이 `gemini_live`에
  도달하는 것까지만 확인했다(총액 상한 $0.000001로 차단한 상태에서).
- 폴백이 일어나면 영수증에 `inference_mode="degraded"`가 박히고 그 테제는
  **판매가 차단된다**(`receipt_complete=False`). 거짓말하지 않는다.

### ③ 시세 — 내장 기준가 ±3% 난수

- **왜**: 실전 경로(`PRICE_SOURCE=live`)는 실측으로 동작을 확인했지만,
  `pay` CLI 서브프로세스가 호출당 최대 40초라 시연 안정성이 떨어진다.
- **어디**: [`app/external.py`](app/external.py) `_BASE_PRICES`, `spot()`
- **전환**: `py -3.13 check_quotes.py` 로 확인 후 `PRICE_SOURCE=live`.
  실전 어댑터는 [`app/adapters/live_quotes.py`](app/adapters/live_quotes.py)
  `spot_live_async()`.

### ④ 미러 주식 토큰 — 실제 주식이 아니다

- **왜**: 국내 자본시장법상 미정리 영역. 실제 브로커 연동은 이 프로젝트 범위 밖.
- **어디**: devnet SPL 토큰 NVDAx/TSLAx/MSFTx/AAPLx.
  [`bootstrap_devnet.py`](bootstrap_devnet.py) `MIRROR_TICKERS`
- **전환**: [`app/adapters/`](app/adapters/)에 실체결 어댑터를 추가하고
  `LEDGER.swap_in()`/`swap_out()`을 그쪽으로 돌린다. 파이프라인은 안 바뀐다.

### ⑤ 외부 에이전트의 테제 구매 — 페이월을 타지 않는다

**이게 가장 정직하게 말해야 할 항목이다.** 먼저 무엇이 x402이고 무엇이
아닌지부터 정확히 구분한다. 아래는 mock 한 사이클의 실측 분류다
(x402 판정 기준: `resource`가 요청 경로이고 `ProofRegistry`가 증빙을 소비함).

| 구분 | 무엇 | 경로 | 금액(µUSDC) |
|---|---|---|---|
| **x402** ✓ | 뉴스 (Exa) | research-agent → external | 2,000 |
| **x402** ✓ | Flash 스크리닝 | research-agent → external | 150 |
| **x402** ✓ | **시그널 구매** | research-agent → revenue-wallet | 50,000 |
| **x402** ✓ | Deep 추론 | research-agent → external | 5,600 |
| **x402** ✓ | 시세 조회 | research-agent → external | 4,000 |
| 직접 이체 | 인지비용 인보이스 | revenue-wallet → research-agent | 250,000 |
| 직접 이체 | 자본 인보이스 | user-treasury → invest-wallet | 17,800,000 |
| 직접 이체 | **외부의 테제 구매 (모의)** | external → revenue-wallet | 200,000 |
| 원자적 스왑 | 미러 토큰 매수 | invest-wallet ↔ market | 22,800,000 |

**미러 주식 토큰 매수는 x402가 아니다 — 그게 맞다.** 스왑은 자산 교환이지
결제가 아니고, USDC 지불과 토큰 수령이 **원자적으로** 묶여야 하기 때문에
의도적으로 페이월 밖에 뒀다. 402를 끼우면 "돈은 나갔는데 토큰을 못 받은"
상태가 가능해진다.

**모의인 것은 `external-sale` 하나다.** 사이클 로그의 이 단계는 x402 결제가
아니라 [`app/main.py`](app/main.py) `bot_cycle` 안의 직접 이체다
(코드 주석에도 `# 외부 에이전트의 테제 구매 (모의)`라고 적혀 있다).
외부가 사는 것은 **시그널이 아니라 테제**라는 점에 주의.

**시그널 판매는 진짜 402를 탄다. 단, 기본 사이클에서는 자기 자신에게 판다.**

```
기본 사이클:  research-agent@bot2 → revenue-wallet@bot2   50,000  ✓소비
              (같은 봇의 지갑 간. 402 절차는 전부 밟지만 자기 주머니 안이다)

봇 간 거래:   research-agent@bot2 → revenue-wallet@bot1   50,000  ✓소비
              (bot2가 bot1에게 실제로 지불. test_cycle.py ③단계에서 실행)
```

즉 **"에이전트가 다른 에이전트에게서 판단을 산다"는 주장은 증명되어 있지만,
그것을 보여주는 것은 기본 사이클이 아니라 `test_cycle.py` ③단계다.**
심사위원이 "자기 돈을 자기가 옮긴 것 아니냐"고 물으면 이 구분으로 답해야 한다.

- **전환**: 외부 구매자가 `POST /bots/{id}/sell/thesis/{id}`를 402 결제로
  호출하게 하면 된다. 판매 창구는 이미 살아 있고 `provenance`까지 반환한다.
  `bot_cycle`의 직접 이체 3줄을 그 호출로 바꾸는 것이 전부다.

### ⑥ pay.sh 게이트웨이(provider.yml) — 비활성

- **왜**: 게이트웨이(1402)가 결제를 받아 우리 서버(8100)로 포워딩하는데,
  우리 `sell/*` 라우트에도 자체 페이월이 걸려 있어 **이중 402**가 된다.
- **어디**: [`provider.yml`](provider.yml), `X402_MODE=paysh`는 예외로 차단됨
- **전환**: 게이트웨이 경유 요청을 식별해 자체 페이월을 우회시키는 내부 토큰
  (`app/main.py`의 `INTERNAL_TOKEN`과 같은 방식)이 필요하다.

### ⑦ 결제 증빙의 온체인 대조 — 미구현

- 증빙은 오프체인 `ProofRegistry`가 관리한다. devnet에서도 마찬가지다.
- **전환**: 이체 memo에 `resource` 해시를 넣고 공급자가 온체인에서 대조.
  [`app/adapters/devnet_ledger.py`](app/adapters/devnet_ledger.py) `_transfer_ix()`에
  memo instruction 추가 + [`app/core/proofs.py`](app/core/proofs.py) `consume()`에서 검증.

### ⑧ mainnet — 전액 미충전

- `SpendGuard`(총액 상한)만 준비돼 있고 실제 지출은 0이다.
- [`app/core/spend_guard.py`](app/core/spend_guard.py), `MAINNET_TOTAL_CAP`

### ⑨ 인증 — 공유 시크릿

- `ADMIN_TOKEN`은 온체인 owner 서명 검증이 아니라 환경변수 문자열이다.
- **전환**: 서명 검증으로 대체. [`app/main.py`](app/main.py) `require_admin()`

### ⑩ 단일 프로세스 전역 상태 (mock 한정)

- uvicorn 워커를 2개 이상 띄우면 mock 원장이 프로세스마다 갈라진다.
- **devnet에서는 해당 없음** — 진실원이 온체인이다.

---

## 6. 발표 시연 순서

### 사전 준비 (발표 시작 전에 미리 끝내둘 것)

devnet은 이체당 2~4초라 라이브로 돌리면 화면이 멈춘 것처럼 보인다.
**아래는 무대에 오르기 전에 돌려두고, 결과 화면만 띄워둔다.**

```powershell
# ① devnet 상태 정합성 (약 3분) — 재실행해도 통화량이 안 변하는 것 확인
py -3.13 bootstrap_devnet.py

# ② devnet 전체 시나리오 (약 3분 40초) — 화면 캡처 또는 터미널 탭에 남겨두기
$env:LEDGER_MODE="devnet"; py -3.13 verify_scenario.py
#   → A3b의 Explorer 링크를 브라우저 탭에 미리 열어둘 것
```

### 라이브 시연 (총 3~4분)

```powershell
# ─────────────────────────────────────────────────────────
# 1. 보안 감사 — 9항목 (1초)
py -3.13 audit.py
#   보여줄 것: 증빙 재사용 차단, 무인증 자금 라우트 4종 403, 원금 유출 차단

# 2. 전체 시나리오 mock — 25개 항목 (15초)
$env:LEDGER_MODE="mock"; py -3.13 verify_scenario.py
#   보여줄 것: 요약표가 한 화면에 다 나온다. 여기서 말할 것은
#   "이게 mock이고, 같은 스크립트를 devnet에서 돌린 게 저 탭입니다"

# 3. devnet 결과 탭으로 전환 — 온체인 증거
#   보여줄 것: [A3b] 시그니처 1개 → Explorer 브라우저 탭
#             "세 수취인이 같은 트랜잭션 안에 있습니다"
#   그리고:   [D5] 같은 tx 2회 재전송, 서로 다른 서명 1개, 증가 1000
#             "재시도해도 두 번 나가지 않습니다"

# 4. 대화형 시연 — 서버를 띄워 심사위원이 직접 만지게
$env:LEDGER_MODE="mock"; py -3.13 -m uvicorn app.main:app --port 8100
#   브라우저 → http://127.0.0.1:8100/docs
#   ① POST /demo/seed            (x-admin-token: dev-token)
#   ② POST /bots/bot2/cycle      (x-admin-token: dev-token)
#   ③ GET  /bots                 (인증 불필요) — 적중률·자급자족 여부
#   ④ GET  /bots/bot2/state      — 룰북·잔고·성과·degraded_decisions
```

### 무대에서 말할 순서 (권장)

1. **"에이전트가 자기 생각값을 스스로 법니다"** → `verify_scenario` 요약표의
   B1a/B1b/B2/B3 네 줄을 가리킨다. 특히 B2 — "사용자 원금에서 API 비용을
   빼는 경로는 코드가 막습니다. 시도해서 막히는 걸 검증에 넣었습니다."
2. **트랙 3요건** → A1/A2/A3. **A2b(거절 3종)를 반드시 짚는다.**
   "승인만 되면 자동출금입니다. 거절되는 걸 보여드립니다."
3. **온체인 증거** → devnet 탭의 A3b Explorer 링크.
4. **정직한 한계** → 5절 ⑤번을 먼저 말한다. 문장은 이렇게:
   *"뉴스·추론·시세·시그널 구매는 전부 진짜 402를 탑니다. 미러 토큰 매수는
   일부러 402 밖에 뒀습니다 — 자산 교환이라 원자성이 더 중요해서입니다.
   아직 모의인 건 하나, 외부 세계가 우리 테제를 사가는 부분입니다.
   대신 봇끼리 사고파는 건 진짜로 돌아갑니다."*
   먼저 말하면 강점이 되고, 질문받고 말하면 약점이 된다.
   - 후속 질문 대비: **기본 사이클의 시그널 구매는 자기 봇의 지갑 간 이동**이다.
     진짜 봇 간 거래(`bot2 → bot1`)는 `test_cycle.py` ③단계에서 나온다.
     물어보면 그 화면을 띄운다.

### 시간이 없으면 자를 순서

`4번 대화형` → `3번 devnet 탭` 순으로 자른다. **1·2번은 자르지 않는다.**

### 실행 시간 실측

| 명령 | mock | devnet |
|---|---|---|
| `audit.py` | 1.1초 | (mock 고정) |
| `test_cycle.py` | 1.2초 | 51초 |
| `verify_scenario.py` | 15초 | 217초 |
| `bootstrap_devnet.py` (재실행) | — | 약 3분 |

---

## 7. 발표 전에 사람이 직접 확인해야 할 것

### 반드시 (안 하면 시연 중 사고)

1. **`INFERENCE_MODE=managed`를 시연에 쓸 것인가 결정** — 실제 Gemini 응답을
   받아본 적이 없다. 쓸 거라면 `pay topup` 후 `py -3.13 check_gemini.py`를
   **먼저** 돌려 실결제 경로를 확인할 것. 확인 못 하면 `mock`으로 갈 것.
2. **`X402_MODE`는 건드리지 말 것** — 켜면 명시적 예외로 전 사이클이 멈춘다.
   (그렇게 설계했다. 조용히 깨지는 것보다 낫다.)
3. **프론트엔드 연동 확인** — 자금 라우트에 인증이 붙었다.
   `/cycle` `/settle` `/replenish` `/close-all` `/manage-positions` 는
   `X-Admin-Token` 헤더가 필요하다. 친구분 코드가 이걸 보내는지 확인.
   응답도 바뀌었다: `/demo/seed` → `{seeded, preserved}`,
   `/bots/{id}/state` → `degraded_decisions` 추가, 테제 판매 → `provenance` 추가.

### 판단이 필요한 것

4. **devnet 고아 봇 자금 처리** — 개발 중 만들어진 봇 지갑이 70개 등록돼 있고
   약 $301+ 이 그 안에 있다. 지갑은 등록돼 있어 회수 가능하지만 그 봇들은
   `BOTS`에 없다. `GET /state`의 통화량 기준선에는 포함되므로,
   심사위원이 물어볼 수 있다. 회수할지 그대로 둘지 정할 것.
5. **킬 스위치 구멍(4절 ③)을 고칠지** — 한 줄이면 고쳐진다. 고치면
   `verify_scenario.py`를 한 번 더 돌려 회귀를 확인할 것.
6. **주간 한도가 시연 길이에 맞는지** — `COG_MANDATE_WEEKLY_CAP` $10.
   이제 진짜 주 단위로 리셋되지만, 장시간 시연이면 값을 올릴 것.

### 눈으로 볼 것

7. **Explorer 링크가 살아 있는지** — devnet은 주기적으로 리셋된다.
   발표 당일 아침에 `verify_scenario.py`를 devnet으로 한 번 돌려
   **새 시그니처**를 확보하고 그 링크를 열어둘 것. 위 문서의 링크는
   작성 시점 것이라 만료될 수 있다.
8. **`solana balance`** — fee_payer SOL이 남아 있는지. 검증 1회에 약 0.02 SOL.

---

## 부록: 검증 방법에 대한 정직한 설명

- **시세를 조작했다.** `verify_scenario.py`의 `set_price()`가 모의 시장의
  기준가를 ±15~20% 움직인다. 손익 부호가 난수면 "손실일 때 분배가 비어 있다"나
  "손절이 발동한다"를 증명할 수 없기 때문이다. **시장을 움직인 것이지 봇의
  판단을 조작한 것이 아니다** — 봇은 여전히 자기 룰북대로 반응한다.
- **D3는 스케줄러 틱을 직접 구동했다.** 대기 없이 `SCHEDULER._tick()`을
  6회 부른다. devnet에서 40초 × 6 = 4분을 기다리지 않기 위해서다.
  코드 경로는 동일하다.
- **D1(audit.py)은 항상 mock으로 돌린다.** 보안 감사 항목은 원장 구현과
  무관하고, devnet으로 돌리면 수 분이 걸린다.
- **devnet C3(스케줄러)는 데모봇 3개를 잠시 멈춘 상태로 관찰했다.**
  한 틱에 4봇을 다 돌리면 틱이 주기를 넘겨 관찰이 무의미해진다.
