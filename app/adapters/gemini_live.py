"""Gemini 추론 어댑터 — pay.sh mainnet 실결제.

    https://generativelanguage.google.gateway-402.com/v1beta/models/{model}:generateContent

[실측 정보 — RECON.md 참조]
    network  : mainnet (localnet 불가. --sandbox 안 통함)
    schemes  : mpp/session, x402/upto
    단가     : gemini-3.6-flash        입력 $0.345/1M  출력 $2.875/1M
               gemini-3.1-pro-preview  입력 $2.30/1M   출력 $13.80/1M

⚠️ 여기서부터 진짜 돈이 나갑니다.
   호출 전에 SpendGuard가 총액 상한을 검사하고, 넘으면 예외로 막습니다.
   봇별 한도·일일 한도가 다 뚫려도 이 마지막 방어선은 남습니다.

[왜 CLI를 거치나]
mpp/session 스킴은 세션 위임을 온체인에 등록해야 한다.
직접 구현하면 키 관리와 서명을 우리가 떠안게 되므로,
검증된 pay CLI에 맡긴다. 실패하면 mock으로 폴백한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

from app import config
from app.core.spend_guard import GUARD
from app.pricing import FLASH, PRO, deep_cost, flash_cost

GATEWAY = "https://generativelanguage.google.gateway-402.com"
TIMEOUT = 90
IS_WINDOWS = os.name == "nt"


class InferenceUnavailable(Exception):
    pass


def enabled() -> bool:
    """INFERENCE_MODE=managed 일 때만 진짜 돈을 쓴다.

    기본값이 mock인 게 중요하다. 실수로 켜지면 안 되니까.

    [2026-08] os.environ 대신 config를 본다. 예전에는 이 함수만
    호출 시점의 환경변수를 읽고 다른 곳은 임포트 시점의 config를 읽어서,
    둘이 어긋나는 조합이 생길 수 있었다. 진실원을 하나로 모은다.

    결제 경로(X402_MODE)와는 무관하다 — 이 스위치는 '추론을 어디서
    받아오나'만 정한다. 두 축을 섞었던 것이 이전 자기모순의 원인이었다.
    """
    return config.INFERENCE_MODE.lower() == "managed"


def _url(model: str) -> str:
    return f"{GATEWAY}/v1beta/models/{model}:generateContent"


def _call(model: str, prompt: str, max_tokens: int, est_cost: int) -> str:
    """실제 결제 후 Gemini 응답 텍스트를 받아온다."""
    # ★ 마지막 방어선. 상한을 넘으면 여기서 멈춘다.
    GUARD.check_and_record(f"gemini:{model}", est_cost)

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }, ensure_ascii=False)

    cmd = ["pay", "fetch", "-X", "POST", _url(model),
           "--content-type", "application/json", "--body", body]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT,
                           encoding="utf-8", errors="replace", shell=IS_WINDOWS)
    except FileNotFoundError:
        raise InferenceUnavailable("pay CLI 없음")
    except subprocess.TimeoutExpired:
        raise InferenceUnavailable(f"{model} 응답 시간 초과")

    for line in reversed((r.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{") and "candidates" in line:
            try:
                data = json.loads(line)
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception:
                continue
    raise InferenceUnavailable(
        f"{model} 응답 파싱 실패: {(r.stderr or r.stdout or '')[:200]}")


# ── 1차 스크리닝 (싼 모델) ──────────────────────────────────────────
def screen(headline: str) -> float:
    """이 뉴스가 투자 관련성이 있는가. 0.0~1.0 점수만 받는다.

    출력을 짧게 강제해서 비용을 최소화한다.
    """
    prompt = (f"다음 뉴스가 주식 투자 판단에 얼마나 유용한지 "
              f"0.0에서 1.0 사이 숫자 하나만 출력하세요. 설명 금지.\n\n{headline}")
    txt = _call(FLASH, prompt, max_tokens=16, est_cost=flash_cost(prompt))
    for token in txt.replace("\n", " ").split():
        try:
            return max(0.0, min(1.0, float(token.strip().rstrip("."))))
        except ValueError:
            continue
    raise InferenceUnavailable(f"관련성 점수 파싱 실패: {txt[:60]}")


# ── 심층 추론 (비싼 모델) ───────────────────────────────────────────
def analyze(ticker: str, headline: str) -> dict:
    """매매 방향·확신도·근거를 받는다. JSON만 출력하도록 강제한다."""
    prompt = (
        f"당신은 주식 분석가입니다. 아래 뉴스를 보고 판단하세요.\n"
        f"종목: {ticker}\n뉴스: {headline}\n\n"
        f'반드시 JSON만 출력하세요. 형식: '
        f'{{"side":"buy" 또는 "sell","confidence":0.0~1.0,"rationale":"한 문장 근거"}}'
    )
    txt = _call(PRO, prompt, max_tokens=200, est_cost=deep_cost(prompt))
    cleaned = txt.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise InferenceUnavailable(f"JSON 없음: {txt[:80]}")
    data = json.loads(cleaned[start:end + 1])

    side = str(data.get("side", "")).lower()
    if side not in ("buy", "sell"):
        raise InferenceUnavailable(f"side 값 이상: {side}")
    return {"side": side,
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.5)))),
            "rationale": str(data.get("rationale", ""))[:200]}


async def screen_async(headline: str) -> float:
    return await asyncio.to_thread(screen, headline)


async def analyze_async(ticker: str, headline: str) -> dict:
    return await asyncio.to_thread(analyze, ticker, headline)
