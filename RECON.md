# 정찰 결과 (2026-07 실측)

`python recon.py` 로 재현 가능. **추측이 아니라 실제 응답 덤프 기반.**

## 확인된 사실

| 항목 | 초기 가정 | 실측 결과 |
|---|---|---|
| 주소 | `pay.sh/api/exa/search` | `debugger.pay.sh/mpp/quote/{TICKER}` |
| 메서드 | POST | **GET** (POST는 403) — ⚠️ 이 라우트 한정 |
| 프로토콜 | x402 | **MPP** (구조 유사) |
| 가격 위치 | 본문 JSON | **www-authenticate 헤더** |
| 인코딩 | 평문 | **base64 (urlsafe)** |

> ⚠️ **메서드는 라우트마다 다르다.** 위 GET 결과는 `debugger.pay.sh` 시세
> 라우트에만 해당한다. Gemini 게이트웨이는 **POST가 정상**이다
> (아래 2026-08 재실측 참조). 라우트별로 확인할 것.

## 실제 402 응답

```
HTTP/1.1 402 Payment Required
www-authenticate: Payment id="...", realm="...", method="solana",
                  intent="charge", request="eyJhbW91bnQ...",
                  description="...", expires="..."

{"type":"https://paymentauth.org/problems/payment-required",
 "title":"Payment Required","status":402,"challengeId":"..."}
```

`request` 를 base64 디코딩하면:

```json
{
  "amount": "10000",
  "currency": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "recipient": "BknQHYzLyyqguToFoAJw1qrZM1NiAzBAsYJnSM4h4Mkg",
  "methodDetails": {
    "decimals": 6, "network": "localnet", "feePayer": true,
    "tokenProgram": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
  }
}
```

## 필드 매핑 (통역)

| 우리 코드 | 실제 MPP |
|---|---|
| `price` | `amount` |
| `pay_to` | `recipient` |
| `resource` | URL path |

→ `app/adapters/paysh_client.py`의 `parse_mpp_challenge()`가 이 변환을 수행.
→ **`paid_fetch`는 수정 불필요.** 어댑터 패턴의 효과.

## 다음 단계

1. `npm install -g @solana/pay` → `pay setup` → `pay whoami`
2. `pay --sandbox curl https://debugger.pay.sh/mpp/quote/AAPL` ← 실결제 확인
3. `pay server demo` ← Payment Debugger 시각화 (발표에 그대로 사용)
4. `inference.py`의 market_quote 하나만 실전 전환 → 성공 후 나머지 확대

## 주의

- 샌드박스의 `network`가 `localnet`이다. devnet/mainnet 전환 시 값이 바뀌므로
  하드코딩하지 말고 챌린지에서 읽을 것.
- Gemini / Exa 라우트는 아직 미확인. pay.sh 카탈로그에서 확인 후 recon.py에 추가.


---

# 추가 실측 (2026-07) — Gemini 게이트웨이

`pay skills show solana-foundation/google/generativelanguage`

```
Gateway: https://generativelanguage.google.gateway-402.com
POST v1beta/models/{modelsId}:generateContent   (metered)
GET  v1beta/models                              (free)
```

## 402 응답 구조 (주가 API와 다름!)

```json
{"network": "mainnet",                    ← localnet 아님
 "schemes": ["mpp/session", "x402/upto"], ← 1회 charge 아님
 "cap": "250000",                          ← 세션당 $0.25
 "settlementAuthority": "delegated",
 "splits": [{"bps": 10000, "recipient": "..."}]}
```

→ `--sandbox`(localnet)로는 결제 불가. mainnet 실제 USDC 필요.

## 실단가

| 모델 | 입력 | 출력 |
|---|---|---|
| gemini-3.6-flash | $0.345 / 1M | $2.875 / 1M |
| gemini-3.1-pro-preview | $2.30 / 1M | $13.80 / 1M |

## 우리 추측과의 오차

| | 추측 | 실측 | 오차 |
|---|---|---|---|
| Flash | 1,500 | **151** | 10배 과대 |
| Deep | 18,000 | **5,575** | 3배 과대 |

→ `app/pricing.py`로 프롬프트 길이 기반 동적 계산으로 교체.
→ 2단계 필터(Flash 선별 → Pro 심층) 효과: **93% 절감** (실단가 기준).

## 네트워크 분리 결론

```
mainnet : Gemini 추론 (선택 불가 — 게이트웨이가 강제)
devnet  : 봇 간 거래 · 판단 판매 · 미러 주식 · 정산 (우리가 통제)
```

---

# 재실측 (2026-08) — Gemini 게이트웨이 402 원문

`POST https://generativelanguage.google.gateway-402.com/v1beta/models/{model}:generateContent`
를 결제 없이 직접 호출해 챌린지만 받아본 결과.

```
status: 402
www-authenticate: 통화별로 5개 챌린지, 전부 intent="session"
본문 schemes: ["mpp/session", "x402/upto"]
```

`request` 를 base64 디코딩:

```json
{
  "cap": "250000",
  "currency": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
  "decimals": 6,
  "minVoucherDelta": "1",
  "network": "mainnet",
  "operator": "BcdwLA62UPEAvRn7AWauMUXKtYMXxdLzTPaSQg5tNaFc",
  "programId": "CHNLxYvVA28MJP9PrFuDXccuoGXAx7jBacfLEkahyGsX",
  "recipient": "BcdwLA62UPEAvRn7AWauMUXKtYMXxdLzTPaSQg5tNaFc",
  "settlementAuthority": "delegated",
  "splits": [{"bps": 10000, "recipient": "Cs2zdfUNonRdRGsiZUQQLdTxzxVvJZmgiX2mpLYKuEqP"}]
}
```

## 이번에 새로 확정된 것

| 항목 | 결과 |
|---|---|
| **메서드** | **POST가 정상** (403 아님). 위 시세 라우트와 다르다 |
| **제시 스킴** | 헤더는 `intent="session"` **만**. `x402/upto` 는 본문 목록에만 있고 헤더 챌린지로는 안 나온다 |
| **통화** | 5종 제시. 첫 번째가 실제 mainnet USDC (`EPjFWdd5…`) |
| **세션 한도** | `cap: 250000` = **$0.25 / 세션** |
| **정산 방식** | `settlementAuthority: "delegated"` + 전용 채널 프로그램 `CHNLxYvV…` |
| **단가** | 본문 `pricing` 에 그대로: flash 0.345/2.875, pro 2.3/13.8 — 위 표와 일치 |

## 세션 위임을 사전 등록해야 하는가 (미검증)

- `pay subscriptions` 는 "**subscription**-intent delegations" 를 다루고,
  `new` 는 온체인 **Plan PDA** 를 요구한다. 게이트웨이가 제시하는 intent 는
  `session` 이고 챌린지에 Plan PDA 가 없다 → **다른 흐름으로 보인다.**
- `pay fetch` 에는 세션 관련 옵션이 하나도 없다 → 402를 만나면 내부적으로
  세션을 여는 것이 설계 의도로 보인다.
- **결론: `gemini_live.py` 의 현재 구현(`pay fetch` 직접 호출)이 맞을
  가능성이 높으나 확인되지 않았다.** 충전 후 `check_gemini.py` 1회로 판별한다.
- 현재 상태: `pay subscriptions list` → `No subscriptions found`,
  mainnet 지갑 SOL 0 / USDC 토큰계정 없음.
