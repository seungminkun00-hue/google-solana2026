# 보안 감사 보고서

`py -3.13 audit.py` 로 재현 가능. **현재 9개 검사 전부 통과.**
(아래 표는 v2 개발 중 실제로 뚫렸다가 막은 7건. 이후 2026-08에 자금
라우트 인증 검사 `5b`, 성과 하한 거절 검사 `8`이 추가되어 총 9개가 되었다.)

## 수정된 취약점

| # | 취약점 | 수정 전 (실측) | 수정 |
|---|---|---|---|
| 1 | 결제 증빙 무한 재사용 | 동일 증빙 5회 → **5회 성공** | `ProofRegistry.consume` 1회성 소비 |
| 2 | 리소스 교차 사용 | 증빙이 payee·amount만 검증 | resource 바인딩 (경로 파라미터 포함) |
| 3 | 알파 유출 | `rationale`에 `buy` 노출 | `for_sale()` 방향 어휘 마스킹 |
| 4 | 통화량 위조 | 총합 100.5억 → **104.9억** | `market` 상대방 경유 + 불변식 assert |
| 5 | 무인증 관리자 | 누구나 킬스위치·시드 | `X-Admin-Token` 검사 |
| 6 | 영수증 오염 | `receipts[-4:]` 슬라이싱 | 호출 반환값만 명시 수집 |
| 7 | 결제 후 실패 시 손실 | 돈만 나가고 물건 못 받음 | `tracker.rollback` 보상 |

## 남은 잔여 리스크 (해커톤 범위 밖, 발표 시 정직하게 언급)

- **단일 프로세스 전역 상태**: `LEDGER`/`MANDATE`가 모듈 전역. uvicorn 워커 2개 이상이면
  프로세스마다 원장이 따로 논다. 실전은 온체인 상태가 단일 진실원이므로 자연 해소.
- **온체인 결제 비가역성**: 결제 후 공급자가 물건을 안 주면 실제 손실.
  `rollback`은 카운터만 되돌린다. 신뢰 공급자 화이트리스트 또는 에스크로 필요.
- **`RECEIPTS`/`SIGNALS` 무한 증가**: 장기 구동 시 메모리 누수. TTL 필요.
- **`threading.Lock` in async**: 단일 프로세스 asyncio에서는 동작하나 의미상 부정확.
- **트랜잭션 크기**: `transfer_many` 수취인이 늘면 Solana 1232바이트 상한 초과 가능.
- **RPC 레이트리밋**: 대시보드 폴링이 공용 RPC를 초과시킴. 유료 RPC 권장.

## 실전 전환 체크리스트

1. ~~`transfer_many`를 단일 트랜잭션 다중 instruction으로 구현~~
   → 완료. `adapters/devnet_ledger.py`. 스텁이던 `solana_ledger.py`는 삭제.
   원자성만으로는 부족해 **멱등성**도 추가했다(`_send_tx` 주석 참조) —
   재시도가 새 블록해시로 새 트랜잭션을 만들어 정산이 두 번 나갈 수 있었다.
2. ~~`commitment='confirmed'` 확인 후 물건 인도~~
   → 완료. 조회·프리플라이트까지 confirmed로 통일.
3. `adapters/paysh_client.py` — `parse_mpp_challenge`를 `paid_fetch`에 연결
   (남은 작업 목록은 `app/inference.py` 의 `_PAYSH_BLOCKERS`)
4. ~~무인증 라우트 정리 — `/settle`, `/replenish`, `/cycle`, `/close-all`~~
   → 완료. `require_operator()` 로 막았다. 내부 파이프라인은 프로세스마다
   새로 만드는 1회용 토큰(`INTERNAL_TOKEN`)으로 통과하므로 관리자 토큰을
   내부에서 돌려쓰지 않는다. `audit.py` 5b가 이걸 검사한다.
5. memo 필드에 resource 해시 삽입 → 공급자 측 결제-리소스 대조
6. `ADMIN_TOKEN` → 온체인 owner 서명 검증으로 대체
