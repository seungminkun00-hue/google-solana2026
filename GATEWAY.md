# 판단 판매 게이트웨이 (요구사항 6)

`pay server`로 우리 API 앞에 결제 문을 세운다.
남의 에이전트가 우리 시그널을 사려면 USDC를 내야 한다.

> ⚠️ **현재 이 경로는 비활성이다.** 게이트웨이(1402)가 결제를 받아 우리
> 서버(8100)로 포워딩하는데, `sell/*` 라우트에 자체 페이월이 걸려 있어
> **이중 402**가 된다. 아래 "구조"의 역할 중복 문제 참조.
> 지금 동작하는 판매 경로는 우리 자체 페이월이고, 통화는 **devnet 자체 민트**다
> (실제 mainnet USDC 아님 — [VERIFICATION.md](VERIFICATION.md) 5절).

## 구조

```
남의 에이전트
    ↓ 402 결제
pay server (:1402)   ← provider.yml 이 정의
    ↓ 통과분만 전달
우리 백엔드 (:8100)  ← FastAPI
```

**중요**: 우리 코드의 `x402_provider.py`(자체 페이월)와 역할이 겹친다.
게이트웨이를 쓰면 그쪽이 결제를 담당하므로, 실전 전환 시
`paywall_dynamic`은 내부 봇 간 거래용으로만 남기거나 제거한다.

## 실행 (터미널 2개)

```powershell
# 터미널 A — 우리 백엔드
py -3.13 -m uvicorn app.main:app --port 8100

# 터미널 B — 결제 게이트웨이
pay server start provider.yml
```

## 구매 테스트

```powershell
# 1) 시그널 ID 확보 (무료 라우트)
pay --sandbox fetch http://127.0.0.1:1402/bots

# 2) 사이클 돌려서 시그널 생성
Invoke-RestMethod -Uri "http://localhost:8100/bots/bot1/cycle" -Method Post

# 3) 유료 구매 — 여기서 402 발생
pay --sandbox fetch -X POST http://127.0.0.1:1402/bots/bot1/sell/signal/<signal_id>
```

## 무료 라우트를 둔 이유

`GET /bots`가 무료인 게 설계 포인트다.
사는 쪽이 **적중률과 트랙레코드를 먼저 확인**할 수 있어야
$0.05를 낼지 판단할 수 있다.

```
무료: "이 봇 적중률 68%, 정산 47건"   ← 신뢰 확인
유료: 실제 시그널 내용                 ← 구매
```

증명 가능성이 곧 판매 가능성이라는 우리 원칙이 URL 구조에 반영된 것.

## 알파 유출 방지

테제 판매본은 `models.py`의 `for_sale()`이 매매 방향(side)과
규모(size)를 마스킹한다. 파는 것은 **확신도와 근거**이지
**최종 결정**이 아니다.

```
우리가 아는 것: "NVDAx buy, 확신 92%"
파는 것:        "NVDAx [REDACTED], 확신 92%"
```

## 남은 확인 사항

- `pay server start`가 devnet 수취를 지원하는지 (기본이 localnet일 수 있음)
- 경로 파라미터 `{bot_id}` 매칭 방식이 우리 FastAPI와 일치하는지
- 게이트웨이 통과 후 원본 헤더가 보존되는지
