"""배포 전용 **비공개** 저장소를 통째로 만들어 준다.

    py -3.13 deploy/make_deploy_repo.py

[왜 저장소를 따로 만드나]
공개 저장소(google-solana2026)에는 `wallets/` 와 `.env` 가 없다 — 개인키
191개와 Gemini·KIS 키라 올리면 안 된다. 그런데 Render·Cloud Run 같은 호스팅은
**git 저장소를 보고 빌드**하므로, 저장소에 없는 파일은 이미지에 들어갈 수 없다.

그래서 배포용 사본을 하나 더 만든다. 이쪽만 **비공개**로 두면
  - 공개 저장소: 심사위원이 읽는 코드 (지금 그대로)
  - 비공개 저장소: 그 코드 + 지갑 + 키  → 여기서 빌드
로 깔끔하게 갈린다. 볼륨도, 시크릿 붙여넣기도, CLI 도 필요 없다.

⚠️ 만들어진 저장소는 **반드시 Private** 으로 올린다. Public 으로 올리면
   devnet 개인키와 API 키가 그대로 노출된다.

[수수료 지불자 경로]
`wallets/devnet.json` 의 `fee_payer_path` 는 이 PC 의 절대경로다
(`C:\\Users\\...\\.config\\solana\\id.json`). 리눅스 컨테이너에는 없으므로
그 키를 `wallets/fee_payer.json` 으로 복사하고 경로를 상대경로로 바꾼다.
이걸 빼먹으면 서버가 기동하다 죽는다. (deploy/pack_secrets.py 와 같은 처리)
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT.parent / "cognitive-economy-deploy"

# 런타임에 필요한 것만. 검증 스크립트·문서·개발 기록은 공개 저장소에 있다.
COPY_FILES = ["requirements.txt"]
COPY_DIRS = ["app"]

# web/ 은 통째로 옮기되 빌드 산출물과 의존성은 뺀다 (컨테이너가 다시 빌드한다)
WEB_SKIP = {"node_modules", "dist", ".vite"}

DOCKERFILE = """\
# 배포 전용 이미지. 공개 저장소의 Dockerfile 과 딱 한 가지가 다르다:
# wallets/ 와 .env 를 **이미지 안에 넣는다.** 이 저장소가 비공개라서
# 가능한 선택이고, 그 덕분에 호스팅 쪽에 볼륨이나 시크릿을 붙이지 않아도 된다.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# `npm ci` 가 아니라 `npm install` 인 이유:
# 잠금파일이 윈도우에서 만들어져서 리눅스 전용 선택 의존성의 하위 가지가
# 비어 있다(@rolldown/binding-wasm32-wasi -> @napi-rs/wasm-runtime ->
# @emnapi/core). `npm ci` 는 플랫폼과 무관하게 트리 전체를 검사하므로
# "Missing @emnapi/core from lock file" 로 죽는다. `npm install` 은 이
# 플랫폼에 실제로 필요한 것만 푼다.
RUN npm install --no-audit --no-fund
COPY web/ ./
RUN node node_modules/vite/bin/vite.js build

FROM python:3.13-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=web /web/dist/ ./web/dist/

# 여기가 공개 저장소와 다른 부분
COPY wallets/ ./wallets/
COPY .env ./.env

ENV PYTHONUNBUFFERED=1 \\
    PYTHONIOENCODING=utf-8 \\
    LEDGER_MODE=devnet \\
    PORT=8100

EXPOSE 8100

# 워커는 반드시 하나다. 봇·포지션·영수증이 프로세스 메모리에 있어서
# 워커가 둘이면 요청마다 다른 세계를 보게 된다.
# ${PORT} 는 호스팅이 주입한다 (Cloud Run 8080, Render 10000 등).
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
"""

DOCKERIGNORE = """\
web/node_modules/
state/
__pycache__/
**/__pycache__/
*.pyc
.git/
*.md
"""

GITIGNORE = """\
# 이 저장소는 비공개다. wallets/ 와 .env 는 **일부러** 올린다 (배포에 필요).
# 아래는 환경마다 달라서 올리지 않는 것들.
state/
web/node_modules/
web/dist/
__pycache__/
*.pyc
"""

README = """\
# cognitive-economy — 배포용 (비공개)

⚠️ **이 저장소는 Private 이어야 합니다.** devnet 개인키와 API 키가 들어 있습니다.

읽을 코드는 공개 저장소에 있습니다 →
https://github.com/seungminkun00-hue/google-solana2026

이쪽은 **호스팅이 빌드해 갈 사본**입니다. 공개 저장소와 다른 점은 하나뿐입니다:
`wallets/` 와 `.env` 가 포함돼 있어서, 볼륨이나 시크릿 설정 없이
`Dockerfile` 만으로 바로 뜹니다.

---

## 배포 (웹 화면만으로, CLI 없이)

### Render — 가장 빠릅니다

1. https://dashboard.render.com → **New +** → **Web Service**
2. 이 저장소를 고릅니다 (Private 저장소도 GitHub 연동하면 보입니다)
3. Language: **Docker** · Instance Type: **Starter 이상**
4. **Create Web Service**

환경변수는 이미 `.env` 로 들어가 있으므로 추가 입력이 없습니다.

> Free 플랜은 15분 놀면 잠들고, 깨어나는 데 50초쯤 걸립니다. 그동안 첫 화면이
> 안 뜹니다. 심사 시간대에는 Starter 플랜을 쓰거나, 미리 한 번 열어서
> 깨워 두시는 편이 안전합니다.

### Google Cloud Run — 해커톤 주제에 맞습니다

1. https://console.cloud.google.com/run → **서비스 만들기**
2. **저장소에서 지속적으로 배포** → GitHub 연동 → 이 저장소 → **Dockerfile**
3. 인증: **인증되지 않은 호출 허용**
4. 컨테이너 탭에서 반드시:
   - **최소 인스턴스 1** — 0 이면 잠들고, 깨어날 때 만들어 둔 봇이 사라집니다
   - **최대 인스턴스 1** — 상태가 프로세스 메모리에 있어 인스턴스가 둘이면
     요청마다 다른 세계를 보게 됩니다

---

## 뜬 뒤 확인

```
curl https://<주소>/health
# {"ok":true,"ledger":"devnet","bots":0,"inference_live":true,"quotes_live":true}
```

`inference_live` 나 `quotes_live` 가 false 면 `.env` 가 이미지에 안 들어간
것입니다. `.dockerignore` 를 확인하세요.

## 갱신

공개 저장소에서 코드를 고친 뒤, 원본 폴더에서:

```powershell
py -3.13 deploy/make_deploy_repo.py
cd ../cognitive-economy-deploy
git add -A; git commit -m "update"; git push
```

호스팅이 push 를 감지해 자동으로 다시 빌드합니다.

## 주의

- **HTTPS 여야 합니다.** 팬텀 지갑은 보안 컨텍스트에서만 주입됩니다.
  Render·Cloud Run 은 기본이 HTTPS 라 그대로 괜찮습니다.
- **devnet SOL 을 채워 두세요.** 세션마다 지갑이 하나, 봇마다 넷씩 늘어납니다.
  수수료 지불자: `Hf6FjLQKxn6rBwJsVP1re8KrgppPyxfYAqrii8Uy1Ewj`
  (https://faucet.solana.com)
"""


def copy_tree(src: pathlib.Path, dst: pathlib.Path, skip: set[str] = frozenset()) -> int:
    n = 0
    for item in src.rglob("*"):
        if any(part in skip or part == "__pycache__" for part in item.relative_to(src).parts):
            continue
        if item.is_dir():
            continue
        target = dst / item.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        n += 1
    return n


def main() -> None:
    cfg_path = ROOT / "wallets" / "devnet.json"
    if not cfg_path.exists():
        raise SystemExit(f"{cfg_path} 없음 - 이 PC 에서 실행하세요.")

    keep_git = DEST / ".git"
    had_git = keep_git.exists()
    if DEST.exists():
        # .git 은 살린다 (원격 설정과 히스토리 유지)
        for item in DEST.iterdir():
            if item.name == ".git":
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    DEST.mkdir(parents=True, exist_ok=True)

    # 1) 코드
    n = 0
    for d in COPY_DIRS:
        n += copy_tree(ROOT / d, DEST / d)
    n += copy_tree(ROOT / "web", DEST / "web", skip=WEB_SKIP)
    for f in COPY_FILES:
        shutil.copy2(ROOT / f, DEST / f)
        n += 1

    # 2) 지갑 - 수수료 지불자 키를 안으로 들여오고 경로를 상대경로로 바꾼다
    (DEST / "wallets").mkdir(exist_ok=True)
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    payer_src = pathlib.Path(cfg["fee_payer_path"])
    if not payer_src.exists():
        raise SystemExit(f"수수료 지불자 키 없음: {payer_src}")
    shutil.copy2(payer_src, DEST / "wallets" / "fee_payer.json")
    cfg["fee_payer_path"] = "wallets/fee_payer.json"
    (DEST / "wallets" / "devnet.json").write_text(
        json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")

    wallets = 1
    for f in (ROOT / "wallets").glob("*.json"):
        if f.name == "devnet.json" or ".bak" in f.name or ".pre-" in f.name:
            continue
        if f.name == "kis_token.json":      # 한 시간이면 만료된다. 서버가 다시 받는다.
            continue
        shutil.copy2(f, DEST / "wallets" / f.name)
        wallets += 1

    # 3) 키
    env = ROOT / ".env"
    if env.exists():
        shutil.copy2(env, DEST / ".env")

    # 4) 배포 파일
    (DEST / "Dockerfile").write_text(DOCKERFILE, encoding="utf-8")
    (DEST / ".dockerignore").write_text(DOCKERIGNORE, encoding="utf-8")
    (DEST / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
    (DEST / "README.md").write_text(README, encoding="utf-8")

    if not had_git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=DEST, check=True)

    print(f"[OK] {DEST}")
    print(f"     코드 {n}개 · 지갑 {wallets}개 · .env {'포함' if env.exists() else '없음(주의)'}")
    print()
    print("다음 (GitHub 에서 Private 저장소를 하나 만드신 뒤):")
    print(f"     cd {DEST}")
    print("     git add -A")
    print('     git commit -m "deploy"')
    print("     git remote add origin https://github.com/<계정>/<비공개저장소>.git")
    print("     git push -u origin main")
    print()
    print("[!] 저장소는 반드시 Private 으로 만드세요. 개인키가 들어 있습니다.")


if __name__ == "__main__":
    main()
