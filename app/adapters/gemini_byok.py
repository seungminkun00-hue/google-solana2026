"""Gemini 추론 어댑터 — 사용자 API 키 직접 호출 (BYOK).

    https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent

[gemini_live.py 와 무엇이 다른가]
gemini_live 는 pay.sh mainnet 게이트웨이에 **실제 돈을 내고** 추론을 산다
(`pay` CLI 필요). 이쪽은 사용자가 발급한 Gemini API 키로 구글에 직접
붙는다. 결제 축은 건드리지 않는다 — API 호출 요금은 내장 x402 페이월을
통해 devnet USDC 로 그대로 흐르고, 여기서 바뀌는 것은 '누가 판단하느냐'
하나뿐이다.

[실측 2026-08-02 — 이 프로젝트에 주어진 키]
    gemini-3.1-pro-preview / gemini-3-pro-preview  → 429, free tier limit: 0
    gemini-3.6-flash       thinkingLevel=low  약 8초
    gemini-3.1-flash-lite  thinkingLevel=low  약 1초
Pro 계열은 호출 자체가 막혀 있어 심층 추론도 Flash 계열로 간다.
선언이 아니라 응답이 진실원이다 — 실제로 답한 모델 ID를 돌려주고,
화면과 영수증에는 그 값이 박힌다.

[thinkingLevel 을 낮추는 이유]
기본값으로 부르면 사고 토큰이 출력 예산을 먹어치워, maxOutputTokens 안에
정작 답이 안 들어온다(실측: 첫 호출 66초 + MAX_TOKENS 로 잘린 쓰레기 응답).
판단의 근거는 우리가 프롬프트에 넣어주므로 긴 사고가 필요 없다.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from app import config

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/"
            "models/{model}:generateContent")

# 한도 회복을 기다리는 최대 시간. 시연 중 화면이 이보다 오래 멈춰 있으면
# 기다리는 것보다 모의 판단으로 내려가 결과를 보여주는 편이 낫다.
MAX_QUOTA_WAIT = float(config.__dict__.get("GEMINI_QUOTA_WAIT", 0) or 35)


class InferenceUnavailable(Exception):
    pass


class QuotaExceeded(InferenceUnavailable):
    """429. 한도는 모델별·분당으로 걸린다.

    retry_after 는 구글이 응답 본문에 적어주는 대기 시간(초)이다.
    "Please retry in 21.6s" 를 그대로 읽는다 — 우리가 짐작한 값보다
    정확하고, 너무 일찍 다시 두드려 한도를 더 깎는 일이 없다.
    """

    def __init__(self, message: str, retry_after: float = 0.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(body: str) -> float:
    m = re.search(r"retry in ([0-9.]+)s", body)
    return float(m.group(1)) if m else 0.0


def enabled() -> bool:
    """키가 있고 모드가 byok 일 때만 진짜 Gemini 를 부른다.

    키만 있고 모드가 mock 이면 부르지 않는다. 반대로 모드만 byok 이고
    키가 없으면 호출할 방법이 없으므로 역시 False 다 — 이 둘을 한 곳에서
    판정해야 '켰다고 생각했는데 안 켜진' 상태가 안 생긴다.
    """
    return (config.INFERENCE_MODE.lower() == "byok"
            and bool(config.GEMINI_API_KEY))


async def _generate(model: str, prompt: str, *, system: str = "",
                    max_tokens: int = 512, json_out: bool = False,
                    history: list[dict] | None = None) -> str:
    """generateContent 한 번. 실패는 전부 InferenceUnavailable 로 모은다.

    부르는 쪽(app/external.py·app/ui.py)은 이 예외를 잡아 모의 판단으로
    내려간다. 추론 하나 못 받았다고 시연이 죽으면 안 되기 때문이다.

    history 는 [{"role": "user"|"model", "text": ...}] 형식의 앞선 대화다.
    이게 없으면 매 질문이 첫 질문이 되어, 봇이 방금 한 말도 기억하지 못한다.
    """
    turns = [{"role": h["role"], "parts": [{"text": h["text"]}]}
             for h in (history or []) if h.get("text")]
    body: dict = {
        "contents": [*turns, {"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingLevel": "low"},
        },
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if json_out:
        body["generationConfig"]["responseMimeType"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=config.GEMINI_TIMEOUT) as client:
            res = await client.post(
                ENDPOINT.format(model=model), json=body,
                headers={"x-goog-api-key": config.GEMINI_API_KEY,
                         "Content-Type": "application/json"})
    except httpx.HTTPError as e:
        # ReadTimeout 은 str(e) 가 비어 있다. 예외 이름을 같이 적지 않으면
        # 로그에 "연결 실패: " 만 남아 원인을 못 찾는다(실제로 그랬다).
        raise InferenceUnavailable(
            f"{model} 연결 실패: {type(e).__name__} {str(e)[:120]}")

    if res.status_code == 429:
        raise QuotaExceeded(f"{model} 할당량 초과(429)", _retry_after(res.text))
    if res.status_code != 200:
        raise InferenceUnavailable(
            f"{model} HTTP {res.status_code}: {res.text[:160]}")

    try:
        parts = res.json()["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError, ValueError) as e:
        raise InferenceUnavailable(f"{model} 응답 형식 이상: {type(e).__name__}")

    # thoughtSignature 만 있고 text 가 없는 조각이 섞여 온다. 텍스트만 잇는다.
    text = "".join(p.get("text", "") for p in parts).strip()
    if not text:
        raise InferenceUnavailable(f"{model} 빈 응답 (사고 예산에 잘렸을 수 있음)")
    return text


async def _generate_or_fallback(model: str, prompt: str, **kw) -> tuple[str, str]:
    """한 모델이 429 면 기본 모델로 한 번 더. (응답, 실제로 답한 모델)

    [왜 필요한가]
    한도는 모델마다 따로 걸린다. 사용자가 3.6 Flash 를 골라두면 그 모델만
    막혀도 판단이 통째로 모의 로직으로 내려갔고, 화면에는 `deep=mock` 과
    "폴백 발생(판매 불가 영수증)" 이 떴다 — 옆에 멀쩡히 도는 모델이 있는데도.

    실제로 답한 모델 ID 를 함께 돌려주는 것이 중요하다. 이 값이 영수증과
    화면에 박히므로, 재시도로 모델이 바뀌었다는 사실도 숨겨지지 않는다.
    """
    fallback = config.GEMINI_FLASH_MODEL
    try:
        return await _generate(model, prompt, **kw), model
    except QuotaExceeded as first:
        pass

    # ① 다른 모델로 즉시 한 번. 한도가 모델별이라 이것만으로 풀릴 때가 많다.
    if model != fallback:
        try:
            print(f"  ↩ {model} 한도 초과 → {fallback} 로 재시도")
            return await _generate(fallback, prompt, **kw), fallback
        except QuotaExceeded as second:
            first = second

    # ② 둘 다 막혔으면 구글이 알려준 시간만큼 기다렸다 한 번 더.
    #
    # 한 사이클 안에서 스크리닝(최대 5회)과 심층추론이 10초 안에 몰리기
    # 때문에 분당 한도를 우리 스스로 때린다. 여기서 포기하면 판단이 모의
    # 로직으로 내려가고, 그 영수증은 팔 수 없게 된다(`deep=mock`).
    # 판단은 이 시스템에서 가장 중요한 한 번이라 기다릴 값어치가 있다.
    wait = min(getattr(first, "retry_after", 0.0) or 20.0, MAX_QUOTA_WAIT)
    print(f"  ⏳ 한도 회복 대기 {wait:.0f}초 후 재시도")
    await asyncio.sleep(wait)
    return await _generate(fallback, prompt, **kw), fallback


def _parse_json(text: str) -> dict:
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < 0:
        raise InferenceUnavailable(f"JSON 없음: {text[:80]}")
    try:
        return json.loads(cleaned[start:end + 1])
    except ValueError as e:
        raise InferenceUnavailable(f"JSON 파싱 실패: {str(e)[:80]}")


# ── 1차 스크리닝 (싼 모델) ──────────────────────────────────────────
async def screen(headline: str) -> tuple[float, str]:
    """이 뉴스가 투자 판단에 얼마나 쓸모 있는가. (점수, 모델ID)"""
    text, model = await _generate_or_fallback(
        config.GEMINI_FLASH_MODEL,
        f"다음 뉴스가 주식 투자 판단에 얼마나 유용한지 0.0에서 1.0 사이 "
        f"숫자 하나만 출력하세요. 설명 금지.\n\n{headline}",
        max_tokens=256)
    for token in text.replace("\n", " ").split():
        try:
            return max(0.0, min(1.0, float(token.strip().rstrip(".")))), model
        except ValueError:
            continue
    raise InferenceUnavailable(f"관련성 점수 파싱 실패: {text[:60]}")


# ── 심층 추론 (판단을 만드는 자리) ──────────────────────────────────
async def analyze(ticker: str, headline: str, *, system: str = "",
                  model: str = "") -> dict:
    """매매 방향·확신도·근거. 봇의 시스템 프롬프트가 여기 실린다.

    system 은 봇을 만들 때 저장해 둔 지침(app/core/prompts.py)이다.
    그래도 최종 거부권은 룰북에 있다 — 여기서 confidence 를 아무리 높게
    불러도 pipeline.rulebook_gate 가 통과시켜야 체결된다.
    """
    raw, model = await _generate_or_fallback(
        model or config.GEMINI_DEEP_MODEL,
        f"종목: {ticker}\n뉴스: {headline}\n\n"
        f"이 뉴스를 근거로 매매 방향을 판단하세요.",
        system=system or "당신은 주식 분석가입니다.",
        max_tokens=1024, json_out=True)
    data = _parse_json(raw)

    side = str(data.get("side", "")).lower()
    if side not in ("buy", "sell"):
        raise InferenceUnavailable(f"side 값 이상: {side!r}")
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        raise InferenceUnavailable("confidence 가 숫자가 아닙니다")
    return {"side": side, "confidence": confidence,
            "rationale": str(data.get("rationale", ""))[:300],
            "model": model}


# ── 대화 (앱의 '대화하기' 탭) ───────────────────────────────────────
async def chat(system: str, question: str, facts: str,
               model: str = "", history: list[dict] | None = None) -> dict:
    """봇과의 대화. 주제 이탈 여부까지 모델이 함께 판정한다.

    {"on_topic": bool, "reply": str} 을 강제하는 이유는, 거절을 서버가
    확인할 수 있어야 하기 때문이다. 자유 문장으로 받으면 "거절했다"고
    믿을 근거가 없고, 화면이 그걸 표시할 수도 없다.

    사실은 질문 **앞**이 아니라 함께 붙인다. 매 턴 최신 상태를 다시
    실어야 대화 도중에 체결이 일어나도 봇이 옛 숫자로 답하지 않는다.
    """
    raw, model = await _generate_or_fallback(
        model or config.GEMINI_FLASH_MODEL,
        f"[현재 상태 — 서버가 원장에서 확인한 값]\n{facts}\n\n"
        f"[사용자 질문]\n{question}",
        system=system, max_tokens=1536, json_out=True, history=history)
    data = _parse_json(raw)
    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise InferenceUnavailable("reply 가 비어 있습니다")

    # 매매 지시. 모델이 지어낼 수 있으므로 모양만 정리해 넘기고,
    # 실행 여부는 서버가 사용자의 말을 다시 보고 결정한다(app/ui.py).
    action = data.get("action")
    if isinstance(action, dict) and str(action.get("side", "")).lower() in ("buy", "sell"):
        try:
            amount = float(action.get("amount_usd") or 0)
        except (TypeError, ValueError):
            amount = 0.0
        action = {"side": str(action["side"]).lower(),
                  "ticker": str(action.get("ticker", "")).strip(),
                  "amount_usd": amount}
    else:
        action = None

    return {"on_topic": bool(data.get("on_topic", True)),
            "reply": reply[:1200], "action": action, "model": model}
