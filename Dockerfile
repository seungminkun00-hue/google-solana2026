# 프론트를 먼저 빌드하고, 그 결과물을 파이썬 이미지로 옮긴다.
# 두 단계로 나누면 최종 이미지에 node_modules(수백 MB)가 안 들어간다.
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
# `npm ci` 가 아니라 `npm install` 인 이유:
# 잠금파일이 윈도우에서 만들어져서 리눅스 전용 선택 의존성의 하위 가지가
# 비어 있다(@rolldown/binding-wasm32-wasi → @napi-rs/wasm-runtime →
# @emnapi/core). `npm ci` 는 플랫폼과 무관하게 트리 전체를 검사하므로
# "Missing @emnapi/core from lock file" 로 죽는다. `npm install` 은 이
# 플랫폼에 실제로 필요한 것만 푼다 — 리눅스에서는 wasm 대신
# binding-linux-x64-gnu 를 쓰므로 그 가지는 아예 필요가 없다.
RUN npm install --no-audit --no-fund
COPY web/ ./
# 배포 빌드는 API 와 같은 출처를 쓴다 (web/.env.production)
RUN node node_modules/vite/bin/vite.js build

FROM python:3.13-slim
WORKDIR /app

# 의존성을 먼저 깔면 코드만 바뀔 때 이 층이 캐시된다
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY --from=web /web/dist/ ./web/dist/

# ⚠️ wallets/ 와 .env 는 **이미지에 넣지 않는다.**
#    개인키와 API 키가 이미지 레이어에 박히면 이미지를 받은 사람이
#    전부 꺼내 볼 수 있다. 실행할 때 볼륨으로 붙이거나 환경변수로 준다.
#    deploy/README.md 참조.

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    LEDGER_MODE=devnet \
    PORT=8100

EXPOSE 8100

# ⚠️ 워커는 반드시 **하나**다. 봇·포지션·영수증이 프로세스 메모리에 있어서
#    워커가 둘이면 요청마다 다른 세계를 보게 된다(한쪽에서 만든 봇이
#    다른 쪽에는 없다). 늘리려면 상태를 밖으로 빼는 작업이 먼저다.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
