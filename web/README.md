# web — 앱 화면 (Figma → 코드)

Figma 파일 `lAbnLgTnx9VfH45NmNQF6c` 의 6개 화면을 그대로 옮기고,
같은 리포지토리의 FastAPI 백엔드에 붙인 것.

**목업의 숫자는 하나도 남아 있지 않다.** 화면에 뜨는 금액·수익률·비중·
승률·API 지출은 전부 원장(`app/core/ledger.py`)과 저널(`app/core/journal.py`)에서
계산돼 나온다. 환율도 실시간이다.

## 실행

터미널 두 개가 필요하다.

```powershell
# ① 백엔드 (리포지토리 루트에서)
py -3.13 -m pip install -r requirements.txt
$env:PYTHONIOENCODING="utf-8"      # ← 콘솔이 cp949 면 기동 로그에서 죽는다
py -3.13 -m uvicorn app.main:app --port 8100

# ② 프론트 (이 폴더에서)
npm install
npm run dev                        # → http://localhost:5173
```

처음이라면 브라우저를 열기 전에 데모 자금을 넣는다.

```powershell
curl.exe -X POST http://127.0.0.1:8100/demo/seed -H "x-admin-token: dev-token"
```

봇이 실제로 일하는 걸 보려면 사이클을 돌리거나 스케줄러를 켠다.

```powershell
curl.exe -X POST http://127.0.0.1:8100/bots/bot2/cycle -H "x-admin-token: dev-token"
curl.exe -X POST "http://127.0.0.1:8100/scheduler/start?interval_seconds=60" -H "x-admin-token: dev-token"
```

화면은 5~10초마다 다시 물어보므로 새로고침하지 않아도 잔고와 포지션이 움직인다.

## 심사위원이 혼자 보는 흐름

목업 옆 패널이 「진행 안내」와 「심사위원 지갑 시연」 두 탭이다
(`components/Sidebar.tsx`). 기본은 안내이고, 7단계를 순서대로 짚어준다.

단계는 세지 않는다. 각 단계에 '끝났다고 볼 조건'을 붙이고 아직 안 끝난 첫
단계를 현재 위치로 본다(`components/GuidePanel.tsx`). 조건의 재료는 전부 실제
상태다 — 봇이 있는가(서버), 체결이 있는가(서버), 어느 화면을 지나왔는가
(라우터, `components/tour.ts`). 순서를 건너뛰어도 안내가 어긋나지 않는다.

- 봇 목록은 비어 있는 상태로 시작한다. 심사위원이 직접 만든다.
- 봇 요약 탭 맨 위 **[지금 일해보기]** → 뉴스 구매부터 체결까지 실제로 한 번.
- 체결되면 화면 위에서 알림이 내려온다(`components/FillToasts.tsx`).
  `GET /ui/events?since=` 를 3초마다 물어보고, 서버가 붙인 seq 로 새것만 받는다.
- 거래 내역과 API 탭의 각 행에 devnet 서명이 붙는다 — Explorer 에서 대조 가능.

## 화면

| 경로 | Figma 노드 | 내용 |
|---|---|---|
| `/` | 1:429 | 총자산(원/USDC), 지갑 선택, 봇 카드 |
| `/bot/:id` | 1:561 | AI 요약 리포트, 자산 추이, 보유 비중 |
| `/bot/:id/trades` | 1:750 | 거래 요약 4지표, 체결 내역 |
| `/bot/:id/apis` | 1:869 | API 사용 요약, 연결된 API, 추천 |
| `/bot/:id/chat` | 1:685 | 봇과 대화 |
| `/bot/:id/settings`, `/bot/new` | 1:1039 | 프로필 + 룰북 편집, 봇 삭제 |

## 구성

```
src/
  api/client.ts     fetch 래퍼 + useApi(폴링) 훅
  api/types.ts      백엔드 app/ui.py 응답 모양
  lib/format.ts     원/달러/퍼센트/수량 서식 (전부 여기를 지난다)
  styles/tokens.css Figma에서 뽑은 색·반경·글꼴
  components/       PhoneFrame · BottomNav · Icon · EquityChart · Donut …
  screens/          화면 6개 + 상세 탭 3개(panels/)
  assets/icons/     Figma 원본 SVG/PNG (손으로 그린 벡터 없음)
```

### 도입부 — 앱을 켜는 흐름

링크를 열면 바로 앱이 뜨지 않는다. 실제로 폰에서 앱을 켜는 순서를 그대로 본다
(`components/IntroBoot.tsx`).

```
iOS 홈 화면            아이콘을 누르면        SOL 스플래시            온보딩
ios-springboard.png  ──────────────────▶  sol-splash.png  ──0.5초──▶  Home
   (슈퍼SOL 2.0)        아이콘 자리에서 확대                              화면
```

- 두 이미지는 402 × 872 로 화면(402 × 874)과 사실상 같아서 그대로 깐다.
- 아이콘 탭 영역은 이미지에서 직접 잰 값이다 — `x 68..131 · y 108..172`.
  이미지를 바꾸면 `IntroBoot.tsx` 의 `ICON` 상수만 다시 재면 된다.
- 타이밍도 같은 파일 상단 상수다: 확대 240ms · 유지 520ms · 페이드 240ms.
- 새로고침하면 처음부터 다시 시작한다 — 시연에서 매번 보여줄 수 있도록
  일부러 기억하지 않는다. 앱 안에서는 화면 맨 아래 **홈 인디케이터 자리를
  누르면** 홈 화면으로 돌아간다.

> 이 도입부는 "신한 앱 안에 들어갔다면" 을 보여주는 **컨셉 시안**이다.
> 제안 상대 밖으로 링크가 돌 가능성이 있으면 화면 어딘가에 그 사실을
> 한 줄 적어두는 편이 안전하다.

### 아이폰 목업

심사용 링크는 데스크톱에서 열리므로, 앱처럼 보이도록 목업 안에서 돌린다
(`components/DeviceFrame.tsx`).

`assets/iphone-frame.png` 는 **화면 안쪽이 투명**하고, 그 구멍이
`x 48..851 · y 46..1793` = **804 × 1748**, 즉 디자인 프레임(402 × 874)의
정확히 2배다. 그래서 PNG를 0.5배로 놓으면 앱이 1:1로 들어간다 —
확대·축소가 없어 글자가 흐려지지 않는다.

- 페이지 자체는 스크롤되지 않는다(`body.framed { overflow: hidden }`).
  스크롤은 화면 안쪽 `.scroll` 에서만 일어나므로 하단 탭이 고정된다.
- 상태바·다이나믹 아일랜드 자리는 `PhoneFrame` 의 `.statusScrim`(54px)이
  덮는다. 디자인에도 프레임마다 같은 배경판(`Rectangle 4197`)이 있다.
- 가로 700px · 세로 620px 미만이면 목업을 떼고 화면을 꽉 채운다(실제 폰).

목업을 바꾸려면 PNG를 교체하고 `DeviceFrame.tsx` 상단의 구멍 좌표 상수만
다시 재면 된다.

### 봇 삭제

`/bot/:id/settings` 아래쪽 **봇 삭제하기** → 확인 시트가 뜬다. 시트는
지우기 전에 서버에 물어본 사실만 보여준다(`GET /ui/bots/{id}/delete-preflight`):
열린 포지션 목록과 각 손익, 지갑에 남은 금액, 사라질 거래 기록 수.

열린 포지션이 있으면 **먼저 청산**한 뒤 지운다. 청산할 주체가 사라진
포지션은 영영 닫히지 않기 때문이고, 백엔드 `DELETE /bots/{id}` 도 같은
이유로 포지션이 남아 있으면 409로 거절한다.

⚠️ **지워도 잔액은 그 봇의 지갑에 남는다.** 이 시스템에는 출금 경로가
없다 — `core/routes.py` 의 허용 경로에 `user-treasury → external` 조합이
없고, `audit.py` 6번이 "사용자 원금 유출 차단"으로 그걸 검증한다.
출금을 붙이려면 그 규칙과 감사 항목을 함께 손봐야 한다. 확인 시트가
이 사실을 그대로 띄운다.

### 알아둘 것 몇 가지

**아이콘은 CSS mask 로 칠한다.** 원본 SVG에는 색이 박혀 있어서 활성/비활성을
바꿀 수 없다. mask 로 두면 모양만 쓰고 색은 `currentColor` 가 정한다.
에셋 경로는 전부 `Icon.module.css` 에 있는데, **React 19 가 인라인 style 안의
`url(...)` 값을 버리기 때문**이다(실측 확인 — 같은 값을 JS로 직접 넣으면
브라우저는 받아들인다).

**아이콘 SVG는 전부 data: URI 로 인라인한다** (`vite.config.ts` 의
`assetsInlineLimit: 40KB`). 기본값 4KB를 넘는 SVG는 별도 파일로 빠지는데,
CSS 안의 그 경로가 상대 경로로 해석되면서 `/bot/bot2` 같은 중첩 라우트에서
404가 났다 — 22KB짜리 설정 기어가 아예 안 보이던 원인이다. data: URI 는
해석할 경로가 없어서 어느 라우트에서든, 어떤 base 로 배포하든 똑같이 뜬다.

**아이콘은 Figma 노드를 통째로 내보내 쓴다.** 조각(벡터 레이어)별로 받아
퍼센트 좌표로 재조립하면 회전·크기가 어긋난다. 노드마다 그룹이 없는
경우(하단 탭·헤더)는 그룹 전체를 한 번 내보낸 뒤 **viewBox 만 잘라서**
아이콘별 파일을 만들었다 — 경로 데이터를 건드리지 않으므로 틀어질 수 없다.
내보낸 파일에 딸려 오는 캔버스 배경 rect(`#F5F5F5`·`#EFF4FB`·`#FEFEFF`·
`#DFE7EF`)는 걷어내야 한다. 안 그러면 mask 가 통째로 칠해져 사각형이 된다.

**npm 스크립트가 `node` 로 실행 파일을 직접 부른다.** 이 리포지토리 경로에
`&` 가 들어 있어서(`…\Desktop\google&solana\…`), cmd 가 PATH 를 그 지점에서
잘라 `vite` 같은 shim 이름을 못 찾는다. 폴더 이름에서 `&` 를 빼면 평범한
`"dev": "vite"` 로 되돌려도 된다.

**자산 추이 곡선은 서버가 관측한 점만 그린다.** 방금 켰다면 점이 두어 개고,
화면도 그렇게 보인다. 3개월 곡선은 3개월을 돌려야 생긴다 — 없는 구간을
그럴듯하게 채우지 않는다.

## 환경변수 (선택)

`.env.example` 참고. 둘 다 없어도 기본값으로 동작한다.

```
VITE_API_BASE=/api          # 기본값. 프록시 대신 직접 붙일 거면 http://127.0.0.1:8100
VITE_ADMIN_TOKEN=dev-token  # 봇 생성·수정·정지에 필요 (백엔드 config.ADMIN_TOKEN)
```

⚠️ 관리자 토큰이 브라우저 번들에 들어간다. 해커톤 데모 구성이고,
실서비스라면 사용자 세션으로 인증한 뒤 서버가 관리자 권한을 대신 써야 한다.
