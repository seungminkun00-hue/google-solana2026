"""봇 프로필 — 룰북이 아닌, 사람이 읽는 설정.

[룰북과 무엇이 다른가]
Rulebook(app/models.py)은 **집행되는** 규칙이다. 허용 종목, 확신도 하한,
손절선처럼 코드가 실제로 검사하는 값만 들어간다. 거기에 자유 텍스트나
표시용 값이 섞이면, 헌법이 무엇을 강제하는지가 흐려진다.

프로필은 그 반대편이다. 봇 이름, 한 줄 설명, 사용자가 쓴 프롬프트,
태그, 위험 성향 라벨 — 화면을 채우고 프롬프트에 실리지만 자금 경로를
막지는 않는 것들. 두 개를 나눠두면 "이 값이 돈을 지키는가"라는 질문에
파일 위치만 봐도 답할 수 있다.

한 가지 예외는 `prompt` 다. 이것은 표시용이 아니라 실제로 Deep 추론
프롬프트에 실린다. 그래도 룰북에 넣지 않는 이유는, LLM에게 주는 지시는
**거부권이 없기** 때문이다. 프롬프트가 뭐라고 하든 최종 판정은 룰북이
한다 — 그 경계를 흐리지 않으려고 일부러 다른 파일에 둔다.
"""
from __future__ import annotations

import threading

from pydantic import BaseModel, Field

# 화면의 '봇 태그'. 첫 번째 태그가 카드에 표시되는 대표 배지가 된다.
TAGS = ["주식", "코인", "부동산", "미술품", "ETF"]
STYLES = ["장기 투자", "중기 투자", "단기 투자"]
GOALS = ["수익 극대화", "안정적 수익", "원금 보존", "배당 중심"]
RISKS = ["매우 보수적", "보수적", "중립", "공격적", "매우 공격적"]
# [2026-08-02] 나라 목록이 아니라 **시장 키** 목록이다. 사용자는 개별 종목이
# 아니라 장을 고르고, 그 안에서 무엇을 살지는 봇이 정한다 — app/core/markets.py.
MARKETS = ["us-nasdaq", "us-semi", "kospi", "kosdaq"]
SESSIONS = ["정규장 (09:00~15:00)", "장전/장후 포함", "24시간"]
CURRENCIES = ["KRW(원)", "USD(달러)", "USDC"]

# 봇이 실제로 어떤 모델로 판단하는가.
#
# [2026-08-02 교정] 예전 목록에는 "Gemini 3.1 Pro" 가 있었는데, 이 프로젝트에
# 주어진 Gemini 키로는 Pro 계열이 호출되지 않는다(free tier limit: 0 → 429).
# 고를 수는 있는데 고르면 매번 폴백하는 선택지는 화면에 두면 안 된다 —
# 사용자는 Pro 로 판단한다고 믿는데 실제로는 모의 판단이 돌아간다.
# 실제로 부를 수 있는 것만 남긴다.
# 목록의 첫 번째가 기본값이다(BotProfile.model). 3.6 Flash 가 아니라
# Flash Lite 를 앞에 두는 이유는 응답 시간 편차 때문이다 — config.py 주석 참조.
MODELS = ["Gemini 3.1 Flash Lite", "Gemini 3.6 Flash"]

# 화면의 표시 이름 → API 모델 ID.
_MODEL_IDS = {
    "Gemini 3.6 Flash": "gemini-3.6-flash",
    "Gemini 3.1 Flash Lite": "gemini-3.1-flash-lite",
}


def model_id(display_name: str) -> str:
    """표시 이름을 실제 호출할 모델 ID로 바꾼다.

    모르는 이름(옛 프로필의 "Gemini 3.1 Pro" 등)이면 빈 문자열을 준다.
    어댑터가 그때 기본 모델로 내려간다 — 없는 모델을 그대로 던져
    404를 받는 것보다 낫다.
    """
    return _MODEL_IDS.get(display_name, "")


class BotProfile(BaseModel):
    bot_id: str
    display_name: str = ""
    tagline: str = Field("", max_length=50)
    prompt: str = Field("", max_length=500)

    tags: list[str] = ["주식"]
    style: str = STYLES[0]

    # 시장 키 목록 (app/core/markets.py). 룰북의 허용 종목은 여기서 펼쳐진다.
    markets: list[str] = ["us-nasdaq"]
    session: str = SESSIONS[0]
    base_currency: str = CURRENCIES[0]

    goal: str = GOALS[0]
    risk: str = RISKS[2]

    notify: bool = True
    auto_reinvest: bool = False
    grant_more_authority: bool = False

    model: str = MODELS[0]

    @property
    def badge(self) -> str:
        """카드 오른쪽 위 배지. 태그와 시장을 합쳐서 정한다.

        '주식' + 국내장이 없음 → '해외주식'. 디자인의 배지 3종
        (주식·코인·해외주식)이 여기서 나온다.
        """
        from app.core.markets import countries_for
        first = self.tags[0] if self.tags else "주식"
        countries = countries_for(self.markets)
        if first == "주식" and countries and "대한민국" not in countries:
            return "해외주식"
        return first


class ProfileStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, BotProfile] = {}

    def get(self, bot_id: str) -> BotProfile | None:
        with self._lock:
            return self._items.get(bot_id)

    def _migrate(self, p: BotProfile) -> BotProfile:
        """옛 프로필의 시장 값을 현재 카탈로그 키로 옮긴다.

        시장 개념이 생기기 전에는 여기에 나라 이름("미국")이 들어 있었다.
        그대로 두면 설정 화면에서 아무 시장도 선택 안 된 것처럼 보인다.
        """
        from app.core.markets import normalize
        fixed = normalize(p.markets)
        if fixed != p.markets:
            p.markets = fixed
        return p

    def ensure(self, bot_id: str, display_name: str = "") -> BotProfile:
        """없으면 기본값으로 만든다.

        데모봇 3개는 코드가 만들고 프로필이 없다. 화면이 프로필을 전제로
        그려지므로, 조회 시점에 기본 프로필을 붙여준다.
        """
        with self._lock:
            p = self._items.get(bot_id)
            if p is None:
                p = BotProfile(bot_id=bot_id, display_name=display_name)
                self._items[bot_id] = p
            elif display_name and not p.display_name:
                p.display_name = display_name
            return self._migrate(p)

    def put(self, profile: BotProfile) -> BotProfile:
        with self._lock:
            self._items[profile.bot_id] = profile
            return profile

    def drop(self, bot_id: str) -> None:
        with self._lock:
            self._items.pop(bot_id, None)

    def dump(self) -> list[dict]:
        with self._lock:
            return [p.model_dump(mode="json") for p in self._items.values()]

    def load(self, rows: list[dict]) -> None:
        with self._lock:
            for r in rows:
                try:
                    p = BotProfile(**r)
                except Exception:            # noqa: BLE001 — 낡은 필드는 버린다
                    continue
                self._items[p.bot_id] = p


PROFILES = ProfileStore()
