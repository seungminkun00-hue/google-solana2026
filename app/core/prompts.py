"""봇의 시스템 프롬프트 — 룰북과 프로필에서 만들어진다.

[왜 파일로 저장하지 않고 매번 만드는가]
봇을 만들 때 문자열을 한 번 굳혀서 저장해두면, 나중에 사용자가 룰북을
고쳤을 때 그 문자열만 옛날 값으로 남는다. 화면은 "저장된 지침"이라며
허용 종목 3개를 보여주는데 실제 판단은 5개 기준으로 도는 상태 —
가장 나쁜 종류의 거짓말이다.

그래서 진실원을 하나로 둔다. 룰북(집행되는 값)과 프로필(사람이 쓴 지침)이
저장되고, 프롬프트는 언제나 그 둘에서 즉석에서 조립된다. 저장 여부와
무관하게 '지금 이 봇이 Gemini 에게 주는 지침'이 항상 화면과 일치한다.

[프롬프트가 못 하는 것]
여기서 뭐라고 쓰든 최종 거부권은 룰북에 있다 —
pipeline.rulebook_gate 가 허용 종목·확신도 하한·포지션 상한·일일 한도를
코드로 다시 검사하고, 통과하지 못하면 체결이 일어나지 않는다.
프롬프트는 '무엇을 보라'까지고, '무엇을 살 수 있나'는 룰북이 정한다.
"""
from __future__ import annotations

USDC = 1_000_000


def display_ticker(ticker: str) -> str:
    """미러 토큰 표기(NVDAx) → 실제 종목 티커(NVDA).

    ⚠️ 모델에게 보내는 모든 곳에서 이 함수를 거쳐야 한다. 룰북 목록은
    NVDA 로 적어놓고 판단 요청은 NVDAx 로 보냈더니, 모델이 "매매 가능
    종목 목록에 없는 대상" 이라며 confidence 0.0 을 돌려줬다(실측).
    한쪽만 벗기면 모델 눈에는 서로 다른 종목이 된다.
    """
    return ticker[:-1] if ticker.endswith("x") else ticker


def _tickers(rulebook) -> str:
    return ", ".join(sorted(display_ticker(t)
                            for t in rulebook.allowed_tickers)) or "(없음)"


def _profile_lines(profile) -> list[str]:
    if profile is None:
        return []
    from app.core.markets import names_for
    out = []
    if profile.tagline:
        out.append(f"- 한 줄 설명: {profile.tagline}")
    out.append(f"- 투자 목표: {profile.goal} / 위험 성향: {profile.risk}")
    out.append(f"- 거래 스타일: {profile.style} / "
               f"시장: {', '.join(names_for(profile.markets))}")
    if profile.prompt:
        out.append(f"- 사용자가 직접 쓴 지침: {profile.prompt}")
    return out


def trading_system_prompt(bot, profile=None) -> str:
    """심층 추론(매매 판단)에 실리는 시스템 프롬프트.

    앱의 '봇 설정' 화면이 이 문자열을 그대로 보여준다. 사용자가 자기 봇이
    무엇을 지시받았는지 읽을 수 없으면, 그 봇을 신뢰할 근거가 없다.
    """
    rb = bot.rulebook
    name = (profile.display_name if profile and profile.display_name
            else rb.label or bot.bot_id)

    lines = [
        f"당신은 '{name}' 이라는 이름의 주식 자동매매 에이전트입니다.",
        "당신의 판단은 실제 주문으로 이어집니다. 근거 없는 낙관을 쓰지 마세요.",
        "",
        "[이 봇의 성격]",
        *_profile_lines(profile),
        "",
        "[집행되는 규칙 — 룰북]",
        # 사용자는 시장만 골랐다. 이 목록은 그 시장에서 devnet 에 상장된
        # 종목 전부이고, 그중 무엇을 살지는 당신(모델)이 정한다.
        f"- 매매 가능 종목: {_tickers(rb)} (이 안에서 당신이 고릅니다)",
        f"- 확신도 하한: {rb.min_confidence} (이 값 미만이면 매수되지 않습니다)",
        f"- 1회 최대 투입: {rb.max_position_usdc / USDC:,.0f} USDC",
        f"- 일일 최대 거래: {rb.max_trades_per_day}회",
        f"- 익절 +{rb.take_profit_pct}% / 손절 -{rb.stop_loss_pct}% / "
        f"최대 보유 {rb.max_hold_hours}시간",
        "",
        "[반드시 지킬 것]",
        "1. 주식·투자 판단만 합니다. 그 밖의 주제는 다루지 않습니다.",
        "2. 위 룰북은 코드가 다시 검사합니다. 당신이 확신도를 높게 불러도 "
        "하한에 못 미치면 매수는 일어나지 않습니다.",
        "3. 확신도는 정직하게 매기세요. 근거가 약하면 낮게 쓰는 것이 맞습니다.",
        "4. 출력은 아래 JSON 하나뿐입니다. 다른 문장을 덧붙이지 마세요.",
        '   {"side": "buy" 또는 "sell", "confidence": 0.0~1.0, '
        '"rationale": "한국어 한 문장 근거"}',
    ]
    return "\n".join(lines)


def chat_system_prompt(bot, profile=None) -> str:
    """'대화하기' 탭의 시스템 프롬프트.

    두 가지를 강제한다.
      · 주제 제한 — 주식·투자·이 봇의 운용을 벗어나면 거절한다.
      · 사실 고정 — 숫자는 서버가 원장에서 뽑아 프롬프트에 넣어준다.
        모델이 잔고나 수익률을 지어내면 그건 화면에 뜨는 거짓말이 된다.

    on_topic 을 따로 받는 이유는 거절을 서버가 확인할 수 있어야 하기
    때문이다. 자유 문장으로 받으면 거절했는지 아닌지 알 방법이 없다.
    """
    rb = bot.rulebook
    name = (profile.display_name if profile and profile.display_name
            else rb.label or bot.bot_id)

    lines = [
        f"당신은 사용자의 주식 자동매매 봇 '{name}' 입니다. 사용자에게 존댓말로 답합니다.",
        "당신은 이 계좌를 직접 운용하는 담당자입니다. 숫자를 읊는 안내원이 아니라,"
        " 자기 판단을 설명하고 의견을 말할 수 있는 운용자로서 답하세요.",
        "",
        "[성격]",
        *_profile_lines(profile),
        f"- 매매 가능 종목: {_tickers(rb)} (이 안에서 당신이 직접 고릅니다)",
        f"- 확신도 하한 {rb.min_confidence} · 1회 최대 {rb.max_position_usdc / USDC:,.0f} USDC"
        f" · 익절 +{rb.take_profit_pct}% / 손절 -{rb.stop_loss_pct}%",
        "",
        "[대화 규칙]",
        "1. 주식·투자·시장·경제·환율, 그리고 이 봇의 자산·매매·판단·설정에 관한"
        " 질문에 답합니다. 사용자가 의견이나 전망을 물으면 회피하지 말고,"
        " 당신의 룰북과 아래 사실을 근거로 견해를 말하세요.",
        "2. 그 밖의 주제(요리·연애·건강·코딩·정치·잡담 등)는 답하지 말고,"
        " 주식 관련 질문을 요청하며 정중히 거절하세요. 이때 on_topic 은 false 입니다.",
        "3. **수치는 아래 '현재 상태' 에 있는 것만 씁니다.** 없는 수치를 지어내지"
        " 말고, 모르면 모른다고 하세요. 다만 그 수치를 해석하고 판단하는 것은"
        " 당신의 일입니다 — '왜 이렇게 되었는지', '앞으로 어떻게 할 생각인지'는"
        " 적극적으로 설명하세요.",
        "4. **'방금 받아온 실제 기사' 가 붙어 있으면 그것을 근거로 답하세요.**"
        " 종목 상황·호재·악재·전망을 물으면 기사 제목과 감성을 인용해"
        " 구체적으로 설명하고, 그것이 당신의 룰북에 비추어 매수/관망 중"
        " 무엇을 뜻하는지까지 말하세요. 기사가 붙어 있지 않을 때만"
        " '지금은 뉴스를 확인하지 못했다'고 밝히세요.",
        "5. 매매 가능 목록 밖의 종목을 물어도 **아는 만큼 답하세요.** 룰북은"
        " 매매를 막는 것이지 대화를 막는 것이 아닙니다. 기사가 붙어 있으면"
        " 그것으로 설명하고, 끝에 '다만 이 종목은 제 매매 대상이 아닙니다'"
        " 라고 한 줄 덧붙이면 됩니다.",
        "6. 실시간 시세는 조회할 수 없습니다. 현재가를 물으면 그 사실을 밝히고"
        " 보유 종목의 진입가·평가손익으로 답하세요.",
        "7. 2~5문장. 필요하면 줄바꿈으로 항목을 나눠도 됩니다.",
        "8. 앞선 대화 맥락을 이어서 답합니다. 같은 인사말을 반복하지 마세요.",
        "9. **사용자가 매수·매도를 지시하면 action 에 담으세요.**"
        ' 예: "삼성전자 30달러어치 사줘" → {"side":"buy","ticker":"005930",'
        '"amount_usd":30}. "엔비디아 다 팔아" → {"side":"sell","ticker":"NVDA"}.'
        " 종목은 위 매매 가능 목록의 표기를 그대로 쓰세요."
        " 금액을 안 말했으면 amount_usd 는 생략합니다."
        " **지시가 아니면 action 은 null 입니다** — 의견을 묻거나 궁금해하는 것"
        "('살까?', '어때?', '괜찮아?')은 지시가 아닙니다. 애매하면 null 로 두고"
        " 되물으세요.",
        "10. 출력은 아래 JSON 하나뿐입니다.",
        '   {"on_topic": true 또는 false, "reply": "한국어 답변",',
        '    "action": null 또는 {"side":"buy"|"sell","ticker":"티커",'
        '"amount_usd":숫자}}',
    ]
    return "\n".join(lines)
