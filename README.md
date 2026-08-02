# Cognitive Economy

**스스로 벌어서 스스로 쓰는 투자 에이전트**
Google Cloud × Solana AI Agentic Hackathon · Track A

**[심사위원 안내](JUDGE.md)** · [English](README.en.md) · [검증](VERIFICATION.md) · [배포](deploy/README.md)

---

## 무엇인가요

사용자가 **시장**만 고르면, 봇이 뉴스와 추론을 **직접 결제해서 사고**, 그 판단으로
주식을 **실제로 매매**하며, 자기가 쓴 비용을 **스스로 벌어 충당**합니다.
사람이 서명하는 순간은 **처음 한 번**뿐입니다.

```
지갑 위임 (서명 1회)
      ↓
뉴스 구매 → 1차 스크리닝 → 심층 추론 → 룰북 검사 → 체결 → 정산
   $0.002    $0.00015      $0.0056                    85/10/5 분배
   └──────── 전부 x402 온체인 결제 · 서명 없음 ────────┘
```

---

## 무엇이 실제인가요

| | 사용하는 것 |
|---|---|
| **결제** | Solana devnet — API 호출 한 건 = 온체인 트랜잭션 한 건 |
| **시세** | 한국투자증권 KIS OpenAPI — 국내·해외 실시간 |
| **추론** | Google Gemini — 매수·매도 판단을 실제 모델이 내립니다 |
| **뉴스** | Alpha Vantage — 대화 중 당일 기사를 읽습니다 |
| **환율** | open.er-api.com — 원화·엔화 실시간 환산 |

화면의 모든 금액·수익률·승률이 원장과 저널에서 계산됩니다. 무언가 꺼져 있으면
화면이 그 사실을 그대로 표시합니다.

### 거래 시장 — 미러 주식 토큰 76종

| 시장 | 종목 | 예 |
|---|---|---|
| 🇰🇷 코스피 | 19 | 삼성전자 · SK하이닉스 · 현대차 |
| 🇰🇷 코스닥 | 20 | 에코프로비엠 · 알테오젠 |
| 🇺🇸 나스닥 | 19 | AAPL · MSFT · NVDA |
| 🇯🇵 도쿄 | 18 | 토요타 · 소니 · 닌텐도 |

devnet에 SPL 토큰으로 발행한 미러 주식이며 **시세는 실제**입니다. 코인으로 사기
때문에 **환전 없이 네 나라 주식을 한 지갑에서** 살 수 있습니다.

---

## 어떻게 동작하나요

| | |
|---|---|
| **x402** | 데이터를 살 때마다 결제합니다. 402 챌린지 → 온체인 결제 → 증빙 첨부 재요청. 증빙은 리소스에 묶이고 **한 번만** 쓰입니다 |
| **룰북** | 사용자가 정한 규칙이 **최종 거부권**을 갖습니다. 모델이 확신도를 높게 불러도 하한 미만이면 체결되지 않습니다 |
| **만다트** | 봇이 스스로 청구서를 발행하고 정책이 심사합니다. 적중률 40% 미만이면 **추가 투입이 거절**됩니다 |
| **영수증** | 어떤 뉴스·어떤 모델이 답했는지 남습니다. 폴백이 일어나면 그 판단은 **팔리지 않습니다** |
| **위임** | SPL `approve` 한 번으로 이후 결제·매매가 서명 없이 집행됩니다. 한도를 넘으면 체인이 거부합니다 |
| **세션** | 브라우저마다 봇과 지갑이 분리됩니다. 여러 명이 같은 링크를 열어도 섞이지 않습니다 |

봇의 지갑은 역할별로 넷이고, 오갈 수 있는 경로가 화이트리스트로 고정돼 있습니다.
`user-treasury → research-agent` 경로는 **존재하지 않습니다** — 사용자 원금이 API
비용으로 새는 일이 구조적으로 불가능합니다.

---

## 화면

Figma 6화면을 실제 백엔드에 붙였고, 데스크톱에서는 아이폰 목업 안에서 돕니다.

**홈** 총자산·봇 카드·충전 · **봇 상세** AI 리포트·자산 추이·거래 내역·API 결제 내역
· **대화** 질문과 주문 · **설정** 시장·프롬프트·룰북

오른쪽에 **진행 안내**와 **실행 로그**가 항상 떠 있습니다.

---

## 실행하기

### 준비물

| | 용도 | 없으면 |
|---|---|---|
| Python 3.13 · Node.js 22 | 실행·빌드 | 필수 |
| Solana devnet 지갑 + SOL | 온체인 결제 | devnet 기동 불가 |
| Gemini API 키 | 실제 추론 | 모의 판단으로 동작 |
| KIS appkey + appsecret | 실시간 시세 | 내장 기준가로 동작 |
| Alpha Vantage 키 (선택) | 대화 중 뉴스 | 뉴스 없이 답변 |

> KIS 키는 [KIS Developers](https://apiportal.koreainvestment.com)에서 발급받습니다.
> **appkey와 appsecret이 모두** 필요합니다.

### `.env`

```ini
INFERENCE_MODE=byok
GEMINI_API_KEY=...
GEMINI_FLASH_MODEL=gemini-3.1-flash-lite
GEMINI_DEEP_MODEL=gemini-3.1-flash-lite

KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ENV=real

ALPHAVANTAGE_API_KEY=...
```

### 처음 한 번

```powershell
py -3.13 -m pip install -r requirements.txt

py -3.13 setup_wallets.py       # devnet 지갑 생성
py -3.13 bootstrap_devnet.py    # USDC·미러 토큰 발행
py -3.13 check_markets.py       # KIS로 종목 검증
py -3.13 mint_markets.py        # 검증된 종목 발행
```

devnet SOL은 [faucet.solana.com](https://faucet.solana.com)에서 받습니다.

### 실행

```powershell
# 백엔드
$env:LEDGER_MODE="devnet"; $env:PYTHONIOENCODING="utf-8"
py -3.13 -m uvicorn app.main:app --port 8100

# 프론트
cd web; npm install; npm run dev      # → http://localhost:5173
```

> `PYTHONIOENCODING=utf-8` 이 없으면 콘솔 인코딩(cp949) 때문에 기동 로그에서 멈춥니다.

### 모드 스위치

세 축이 독립입니다.

```powershell
$env:INFERENCE_MODE="byok"   # byok(사용자 키) · mock
$env:LEDGER_MODE="devnet"    # devnet(실제 SPL) · mock
$env:PRICE_SOURCE="kis"      # kis(실시세) · mock
```

---

## 검증

```powershell
py -3.13 verify_scenario.py    # mock 23/0 · devnet 25/0
py -3.13 audit.py              # 보안 감사 9/9
```

증명하는 것 중 일부입니다.

- 사용자 원금이 인지비용으로 새는 경로가 **없습니다**
- 결제 증빙 재사용·교차 사용이 **거부**됩니다
- 정산 3건이 **하나의 온체인 트랜잭션**을 공유합니다
- 확정 조회가 실패해도 이체는 **한 번만** 집행됩니다
- 재시작해도 봇과 자금이 **살아남습니다**

항목별 증거 → [VERIFICATION.md](VERIFICATION.md)

---

## 구조

```
app/
  main.py     매매·정산·수동주문 · 프론트 서빙
  ui.py       앱 API (/ui/*)          judge.py  지갑 위임 (/judge/*)
  core/       routes(경로 규칙) · mandate(심사) · receipts(영수증)
              markets(시장) · session(격리) · prompts(AI 지침)
  adapters/   devnet_ledger · kis_quotes · gemini_byok · news · fx
web/          React + TypeScript (Vite)
```

---

## 문서

| | |
|---|---|
| [JUDGE.md](JUDGE.md) | 심사위원 안내 — 준비물과 진행 순서 |
| [VERIFICATION.md](VERIFICATION.md) | 검증 25항목 증거 |
| [deploy/README.md](deploy/README.md) | 배포 절차 |
| [WEB-STATUS.md](WEB-STATUS.md) | 개발 기록 |
| [web/README.md](web/README.md) | 화면 구현 상세 |
