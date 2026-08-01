# 실전 전환 체크리스트

## ✅ 완료
- [x] mock 전 사이클 + 보안감사 8항목
- [x] 402 실측 (recon.py) — MPP 구조 해독
- [x] pay CLI 설치 + 샌드박스 실결제 완주
- [x] Gemini 게이트웨이 발견 + 실제 단가 확보
- [x] CORS (프론트 연동)
- [x] devnet 지갑 12개 생성

## 🔥 SOL 없어도 지금 할 수 있는 것
- [ ] config 가격표를 실측값으로 교정
- [ ] 토큰 수 기반 동적 가격 계산 함수
- [ ] 친구에게 API 명세 전달 (/docs)
- [ ] provider.yml 내용 확인 (판단 판매용)

## ✅ 코드 작성 완료 (실행만 남음)
- [x] devnet_ledger.py — 진짜 SPL 이체 어댑터
- [x] transfer_many 원자적 구현 (단일 tx 다중 instruction)
      실측: transfer 3건 = 379 bytes / 상한 1232 → 최대 8건 안전
- [x] bootstrap_devnet.py — USDC + 미러주식 발행, ATA 생성, 초기 분배

## ✅ devnet 실전 (완료 2026-08)
```powershell
$env:LEDGER_MODE="devnet"     # main.py 수정 불필요 — 환경변수로 교체된다
py -3.13 test_autopilot.py    # 사람 개입 없이 매수·청산·정산 완주 확인
```
- [x] DevnetLedger 연결 (`LEDGER_MODE=devnet`)
- [x] 온체인 원자적 스왑 + 85/10/5 원자적 분배 실측
- [x] `POST /bots` — 봇 지갑 4개 + 미러 ATA 4개를 단일 트랜잭션으로 생성
- [x] **멱등 전송** — 재시도가 정산을 두 번 집행하던 결함 수정
- [x] **재시작 생존** — 봇·영수증·포지션 복원 (`state/app_state.json`)

## 🔧 다음에 고칠 것 (진단 순서)
- [ ] 만다트 `weekly_paid` 리셋 없음 → 장시간 구동 시 조달이 영구 중단
- [ ] `request_capital`이 노출 대신 잔고를 넘김 (`BOOK.exposure` 미사용)
- [ ] 무인증 라우트: `/settle` `/replenish` `/cycle` `/close-all`
- [ ] `spot()`이 동기 `pay` CLI를 호출해 이벤트 루프를 막음
      (`live_quotes.spot_live_async` 가 있는데 안 쓰임)
- [ ] `PROOFS.gc()` 호출자 없음 → 증빙 레코드 무한 증가
- [ ] 시세 폴백이 조용해서 손익 출처가 영수증에 안 남음

## 💵 mainnet (맨 마지막, $5)
- [ ] audit.py ✅ 9개 재확인
- [ ] 한도를 최소로 낮추기 (RESEARCH_DAILY_CAP)
- [ ] pay topup $5
- [ ] Gemini Flash 1회 실결제

## 실측 데이터 (RECON.md 참조)
```
gemini-3.6-flash        입력 $0.345/1M   출력 $2.875/1M
gemini-3.1-pro-preview  입력 $2.30/1M    출력 $13.80/1M
주가 API (샌드박스)      $0.01/call  — localnet, 결제 성공
```

## 실행
```powershell
py -3.13 test_cycle.py       # 전 사이클
py -3.13 audit.py            # 보안 9항목
py -3.13 recon.py            # 402 정찰
py -3.13 setup_wallets.py    # 지갑 생성
py -3.13 -m uvicorn app.main:app --port 8100
```
