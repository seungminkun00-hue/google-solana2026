# 배포

서비스는 **하나**다. FastAPI 가 API 와 화면을 같이 준다
(`app/main.py` 맨 아래 SPA 폴백). 그래서 CORS 도, 프론트 주소 설정도 없다.

```
심사위원 → https://<도메인>/          화면
                        /ui/*        앱 API
                        /judge/*     지갑 시연
                        /health      헬스체크
```

> ⚠️ **GitHub Pages 로는 안 된다.** Pages 는 정적 파일 서버라 파이썬을 못 돌린다.
> 루트에 `index.html` 이 없으니 README.md 를 대신 렌더링해서, 켜 두면
> "웹사이트를 눌렀는데 README 만 나온다" 가 된다. Vercel·Netlify 도 같은 이유로
> 안 된다(6절 표 참조). 서버가 도는 곳이어야 한다.

---

## 0. 가장 빠른 길 — 배포 전용 비공개 저장소

호스팅은 **git 저장소를 보고 빌드**한다. 그런데 기동에 필요한 `wallets/` 와
`.env` 는 공개 저장소에 없다(1절). 그래서 볼륨을 붙이거나 시크릿을 넣는
작업이 생기는데, 그걸 통째로 없애는 방법이 있다 — **배포용 사본을 비공개
저장소로 하나 더 두는 것**이다.

```powershell
py -3.13 deploy/make_deploy_repo.py     # → ../cognitive-economy-deploy/
```

만들어진 폴더는 `wallets/` 와 `.env` 를 **포함**하고, 그것들을 이미지에
넣는 Dockerfile 이 따로 들어 있다. GitHub 에서 **Private** 저장소를 하나
만들어 push 한 뒤, Render 나 Cloud Run 에서 그 저장소를 고르면 끝이다.
볼륨도 시크릿도 CLI 도 필요 없다.

| | 공개 저장소 | 비공개 배포 저장소 |
|---|---|---|
| 용도 | 심사위원이 읽는 코드 | 호스팅이 빌드해 가는 사본 |
| `wallets/` · `.env` | 없음 | **있음** |
| Dockerfile | 키를 안 넣음 | 키를 이미지에 넣음 |

> ⚠️ 배포 저장소는 **반드시 Private**. Public 으로 올리면 devnet 개인키와
> Gemini·KIS 키가 그대로 노출된다.

아래 1~5절은 볼륨 방식(수동 배포)을 쓸 때의 절차다.

---

## 1. git 에 올라가지 않는 것들 — 이게 핵심이다

`.gitignore` 가 막는 것이 곧 **기동에 필요한 것**이다. 저장소만 클론하면
서버는 `wallets/devnet.json 없음` 으로 죽는다.

| 항목 | 없으면 | git |
|---|---|---|
| `wallets/` — devnet 키페어 191개 + 민트 설정 | **기동 실패** | ❌ 개인키 |
| `.env` — `GEMINI_API_KEY` · `KIS_APP_SECRET` | 실추론·실시세 꺼짐(모의로 동작) | ❌ 비밀 |
| `state/` — 봇·포지션·저널 | 빈 상태로 시작 (그건 괜찮다) | ❌ 환경마다 다름 |

묶어서 옮긴다.

```powershell
py -3.13 deploy/pack_secrets.py      # → deploy/secrets.tar.gz (약 61KB)
```

이 스크립트가 하는 중요한 일 하나: `devnet.json` 의 `fee_payer_path` 가
이 PC 의 절대경로(`C:\Users\...\.config\solana\id.json`)라 리눅스에서는
없다. 그 키를 묶음 안으로 복사하고 경로를 `wallets/fee_payer.json` 으로
바꿔준다. 이걸 안 하면 서버가 기동하다 죽는다.

> ⚠️ `secrets.tar.gz` 는 개인키를 담고 있다. git·채팅·메일로 보내지 말 것.
> 서버에 올릴 때는 `scp` 나 호스팅 업체의 파일/시크릿 기능을 쓴다.

### `.env` 파일 없이 환경변수로만 줘도 됩니다

호스팅 업체의 **Environment / Secrets** 화면에 넣는 방식이 더 안전합니다.
`app/config.py` 가 `os.environ.setdefault` 로 읽으므로, 이미 설정된
환경변수가 `.env` 파일보다 **우선**합니다.

```
INFERENCE_MODE=byok
GEMINI_API_KEY=...
GEMINI_FLASH_MODEL=gemini-3.1-flash-lite
GEMINI_DEEP_MODEL=gemini-3.1-flash-lite
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ENV=real
ALPHAVANTAGE_API_KEY=...
LEDGER_MODE=devnet
PYTHONIOENCODING=utf-8
```

실측 확인(2026-08-03): `.env` 를 지우고 위 값들을 환경변수로만 줘서 기동했더니
`{"inference_live":true,"quotes_live":true}` 가 나왔습니다. 즉 배포 묶음에서
`.env` 는 빼도 되고, **지갑(`wallets/`)만 있으면** 됩니다.

> ⚠️ Gemini 무료 티어는 **분당 호출 제한**이 있습니다. 심사위원 여러 분이
> 동시에 대화하시면 429 가 나고 그 답변은 '원장 기반 응답' 으로 내려갑니다
> (화면이 그 사실을 표시합니다). 시연이 몰릴 예정이라면 결제 계정으로
> 올려두시는 편이 안전합니다.

---

## 2. Docker 로 띄우기

```bash
docker build -t cognitive-economy .

docker run -d --name ce -p 80:8100 \
  -v /srv/ce/wallets:/app/wallets \
  -v /srv/ce/state:/app/state \
  --env-file /srv/ce/.env \
  -e LEDGER_MODE=devnet \
  cognitive-economy
```

서버에서 미리 풀어둔다.

```bash
mkdir -p /srv/ce && tar -xzf secrets.tar.gz -C /srv/ce
# → /srv/ce/wallets/*, /srv/ce/.env
```

**볼륨이 두 개인 이유**
- `wallets/` 를 볼륨에 두지 않으면 재배포 때 지갑이 사라진다. devnet 에
  자금이 남은 채 **그걸 움직일 키가 없어진다** = 자금 좌초. 가장 위험한 실수다.
- `state/` 가 없으면 재배포마다 심사위원이 만든 봇이 사라진다.

---

## 3. Docker 없이 (VM·EC2 등)

```bash
git clone <repo> && cd cognitive-economy
tar -xzf secrets.tar.gz                      # wallets/ 와 .env 가 생긴다
pip install -r requirements.txt

cd web && npm ci && npx vite build && cd ..  # web/dist 생성
LEDGER_MODE=devnet PYTHONIOENCODING=utf-8 \
  python -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --workers 1
```

---

## 4. 반드시 지킬 것

**워커는 하나다.** 봇·포지션·영수증이 프로세스 메모리에 있다. 워커가 둘이면
한쪽에서 만든 봇이 다른 쪽에는 없어서 요청마다 다른 세계를 보게 된다.
`--workers 1` 을 빼지 말 것. 늘리려면 상태를 밖으로 빼는 작업이 먼저다.

**HTTPS 가 필요하다.** 팬텀 지갑은 보안 컨텍스트에서만 주입된다. `http://`
로 열면 「팬텀이 감지되지 않았습니다」가 뜨고 지갑 시연이 통째로 막힌다.
localhost 는 예외라 개발 중에는 문제없다.

**관리자 토큰을 바꾼다.** `ADMIN_TOKEN` 기본값이 `dev-token` 이고, 프론트
번들에도 같은 값이 들어간다. 바꿀 거면 서버 환경변수와 `web/.env.production`
의 `VITE_ADMIN_TOKEN` 을 **같이** 바꿔야 한다. 다만 브라우저 번들에 들어가는
이상 비밀이 아니다 — 실제 보호는 세션 격리(`app/core/session.py`)가 한다.

**관제 화면을 켜두려면 `OWNER_TOKEN` 을 준다.** 없으면 `/owner` 라우트가
아예 등록되지 않는다(404). 기존 `ADMIN_TOKEN` 은 프론트 번들에 들어가는
공개값이라 이 화면을 지킬 수 없어서 별도 값을 쓴다.

**관제 기록은 볼륨이 있어야 재배포를 넘긴다.** `state/owner_log.<mode>.jsonl`
에 한 줄씩 쌓이는데, 볼륨 없이 배포하면 컨테이너가 새로 뜰 때 사라진다.
심사 중에 배포를 건드리지 않으면 문제되지 않지만, 기록을 남겨야 한다면
`state/` 를 볼륨에 둘 것.

**devnet SOL 을 채워둔다.** 세션마다 지갑이 하나씩, 봇마다 넷씩 늘어난다.
수수료 지불자: `Hf6FjLQKxn6rBwJsVP1re8KrgppPyxfYAqrii8Uy1Ewj`
(https://faucet.solana.com)

---

## 5. 뜬 뒤 확인

```bash
curl https://<도메인>/health
# {"ok":true,"ledger":"devnet","bots":0,"inference_live":true,"quotes_live":true}
```

`inference_live` 나 `quotes_live` 가 `false` 면 `.env` 가 안 읽힌 것이다.
화면은 그래도 동작하지만 모의 판단·내장 기준가로 돈다 — 그 사실이
홈 화면 하단 배지와 안내 패널에 그대로 표시된다.

---

## 6. 호스팅 고르기

| | 맞는가 | 이유 |
|---|---|---|
| Fly.io · Railway · Render | ✅ | 영구 볼륨 + HTTPS + Docker. 볼륨을 꼭 붙일 것 |
| Google Cloud Run | ✅ | 해커톤 주제에 맞다. **최소·최대 인스턴스를 1** 로 |
| Cloudtype · Koyeb | ✅ | 국내/무료 티어. 볼륨 지원 확인 필요 |
| **GitHub Pages** | ❌ | 정적 파일만. 파이썬이 안 돈다 → README 만 뜬다 |
| Vercel · Netlify | ❌ | 정적/서버리스라 메모리 상태와 볼륨이 없다 |
| EC2 · GCE 소형 VM | ✅ | 가장 단순. 다만 HTTPS 를 직접(Caddy·nginx) |

무료 티어의 **자동 슬립**을 주의할 것. 프로세스가 자면 메모리 상태가
날아가는데, `state/` 에서 복원되므로 봇은 살아난다. 다만 첫 요청이 느리고
`SIGNALS`/`THESES`(판매 카탈로그)는 비어서 시작한다.
