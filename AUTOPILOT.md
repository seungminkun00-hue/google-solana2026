# 자동 투자 봇 — 사용자 흐름

## 1. 사용자가 앱에서 봇을 만든다

```
POST /bots        (X-Admin-Token 필요)
{
  "owner": "민수",
  "label": "반도체 단타",
  "deposit_usdc": 300,

  "tickers": ["NVDA", "TSLA"],      ← 매수 규칙
  "min_confidence": 0.8,
  "max_position_usd": 40,
  "max_trades_per_day": 5,

  "take_profit_pct": 5.0,            ← 매도 규칙
  "stop_loss_pct": 3.0,
  "max_hold_hours": 24
}
```

**종목 검증**: smartmoney.market SEC 추적 목록(1,045종목)과 대조.
없는 종목이면 400으로 거부한다. 그냥 받으면 봇이 영원히 아무것도
못 사는데 사용자는 이유를 모른다.

**예치금 처리**: 전액 트레저리로. 매매 자금은 자본 만다트가 청구해서
받아간다. 처음부터 다 넘기지 않는 게 핵심 — 성과가 40% 밑이면 추가
투입이 거절된다.

**research-agent는 0에서 시작**한다. 반드시 인보이스로만 조달해야
"스스로 벌어서 쓴다"가 참이 된다.

## 2. 자동 실행을 켠다

```
POST /scheduler/start?interval_seconds=300
```

이후 사람 개입 없이 5분마다 전 봇을 순회한다.

### 매 주기마다

```
1. 일일 카운터 리셋 (날이 바뀌었으면)
2. 열린 포지션 점검 → 룰북 조건이면 청산   ← 파는 게 먼저
3. 새 기회 탐색 → 조건 맞으면 매수
```

**청산이 매수보다 먼저**인 이유: 자금 효율. 팔아서 확보한 돈으로
새로 살 수 있다.

## 3. 청산은 룰북이 판단한다

```python
if pct <= -stop_loss_pct:      → 손절
if pct >= +take_profit_pct:    → 익절
if held >= max_hold_hours:     → 시간 초과 정리
```

사람이 자는 동안 손실을 끊고 이익을 확정하는 것이 자동화의 본질.

## 4. 안전장치

| 장치 | 동작 |
|---|---|
| 예외 격리 | 봇 하나가 터져도 나머지는 계속 |
| 연속 실패 5회 | 그 봇만 자동 정지 (`auto_killed`) |
| 킬 스위치 | `POST /admin/kill/{bot_id}` |
| 전체 청산 | `POST /bots/{bot_id}/close-all` |
| 스케줄러 정지 | `POST /scheduler/stop` |
| mainnet 총액 상한 | `SpendGuard` — 진짜 돈 마지막 방어선 |

**연속 실패 자동 정지**가 중요하다. 고장난 봇이 영원히 재시도하며
인지비용을 태우는 걸 막는다. 실측에서 잔고 없는 봇 3개가 5회 실패 후
스스로 멈추는 것을 확인했다.

## 5. 조회

```
GET /bots                    전체 리더보드
GET /bots/{id}/state         상세 (룰북·잔고·성과·보유)
GET /scheduler/status        가동 상태 + 최근 활동 20건
```

## 검증

```powershell
py -3.13 test_autopilot.py
```

실측 결과 (5초 주기, 25초 관찰):
```
[ 5s] tick=1  보유=1  거래=1  결정=1  정산=0
[10s] tick=2  보유=1  거래=2  결정=2  정산=1
[15s] tick=3  보유=0  거래=2  결정=3  정산=2   ← 자동 청산
[20s] tick=4  보유=1  거래=3  결정=4  정산=2
[25s] tick=5  보유=1  거래=4  결정=5  정산=3
```

사람 개입 없이 매수·보유·청산이 반복됐다.
