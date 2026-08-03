"""거래 시장 — 사용자가 고르는 단위.

[왜 개별 종목이 아니라 시장인가]
자동매매의 요점은 '무엇을 살지 AI가 정한다' 는 것이다. 사용자가 종목까지
찍어주면 남는 건 매매 타이밍뿐이고, 그건 자동매매라기보다 예약주문에 가깝다.
그래서 사용자는 **어느 장에서 놀지**만 정하고, 그 안에서 무엇을 살지는
봇이 뉴스와 추론으로 고른다.

[룰북은 그대로 남는다]
시장 선택은 곧 룰북의 허용 종목 목록으로 펼쳐진다(`tickers_for`).
즉 집행되는 규칙은 여전히 '이 종목만' 이라는 화이트리스트이고,
audit.py 와 verify_scenario.py 가 검증하는 성질도 그대로다.
바뀐 것은 그 목록을 사람이 손으로 적느냐, 시장에서 펼쳐지느냐뿐이다.

[목록을 손으로 적지 않는 이유]
종목 코드·이름·거래소를 코드에 박으면 반드시 틀린다. 상장폐지·합병·코드
변경이 있고, 무엇보다 **시세가 오지 않는 종목**을 넣으면 그 종목은 영원히
살 수 없다. 그래서 두 파일의 교집합만 시장이 된다.

    wallets/market_candidates.json   KIS 로 시세가 실제로 온 종목 (check_markets.py)
    wallets/devnet.json[mirror_mints] devnet 에 민트가 있는 종목 (mint_markets.py)

둘 다 있어야 '거래 가능'이다 — 시세가 있어야 값을 매기고, 민트가 있어야
살 수 있다. 하나라도 없으면 그 시장은 거래 불가로 표시되고 화면이 이유를
그대로 띄운다. 고를 수 있게 해놓고 아무것도 못 사면 그건 거짓말이다.
"""
from __future__ import annotations

import json
import pathlib
import re

WALLET_DIR = pathlib.Path("wallets")
CANDIDATES_PATH = WALLET_DIR / "market_candidates.json"
DEVNET_PATH = WALLET_DIR / "devnet.json"

# 검증 파일이 아직 없을 때 쓰는 최소 구성. bootstrap 직후의 4종이고,
# 이 값들은 실측된 기준가(app/external.py 의 _BASE_PRICES)를 가진다.
_FALLBACK = {
    "us-nasdaq": {
        "name": "미국 · 나스닥",
        "ok": [{"ticker": t, "name": t, "spec": {"excd": "NAS"}}
               for t in ("NVDA", "MSFT", "AAPL", "TSLA")],
    },
}

# flag 는 화면 곳곳에 붙는다. 코인으로 사면 환전 없이 어느 나라 주식이든
# 같은 지갑에서 산다는 것이 이 프로젝트의 주장인데, 종목 코드만 보면
# 그게 안 읽힌다. 국기 하나면 "지금 도쿄 주식을 샀다"가 즉시 전달된다.
_MARKET_META = {
    "kospi":     {"country": "대한민국", "flag": "🇰🇷",
                  "desc": "시가총액 상위 대형주."},
    "kosdaq":    {"country": "대한민국", "flag": "🇰🇷",
                  "desc": "성장주 중심 시장."},
    "us-nasdaq": {"country": "미국", "flag": "🇺🇸",
                  "desc": "AI·빅테크 중심."},
    "jp-tse":    {"country": "일본", "flag": "🇯🇵",
                  "desc": "도쿄증권거래소 대형주."},
}


def _load_raw() -> dict:
    try:
        return json.loads(CANDIDATES_PATH.read_text(encoding="utf-8"))
    except Exception:                                     # noqa: BLE001
        return _FALLBACK


def _minted() -> set[str]:
    """devnet 에 민트가 있는 미러 토큰. 없으면 살 수 없다."""
    try:
        cfg = json.loads(DEVNET_PATH.read_text(encoding="utf-8"))
        return set(cfg.get("mirror_mints", {}))
    except Exception:                                     # noqa: BLE001
        return set()


# 해외 종목의 한국어 별칭.
#
# [왜 손으로 적나]
# 국내 종목은 KIS 가 "삼성전자" 처럼 한글 이름을 주지만, 해외 종목은
# 이름 자리에 티커를 그대로 준다(NVDA → "NVDA"). 그래서 한국어로 쓰는
# 화면인데 "엔비디아 사줘" 를 못 알아들었다.
#
# 지어낸 이름이 아니라 국내에서 통용되는 표기이고, 짧은 것(3글자 미만)은
# 넣지 않는다 — find_tickers 가 3글자 미만을 건너뛰기도 하고, 짧은 별칭은
# 문장 어디에나 우연히 들어가 오탐을 낸다.
_KO_ALIASES: dict[str, tuple[str, ...]] = {
    "AAPL": ("애플",),
    "MSFT": ("마이크로소프트", "마소"),
    "NVDA": ("엔비디아",),
    "GOOGL": ("구글", "알파벳"),
    "AMZN": ("아마존",),
    "META": ("메타", "페이스북"),
    "AVGO": ("브로드컴",),
    "TSLA": ("테슬라",),
    "COST": ("코스트코",),
    "NFLX": ("넷플릭스",),
    "AMD": ("에이엠디",),
    "PEP": ("펩시", "펩시코"),
    "ADBE": ("어도비",),
    "CSCO": ("시스코",),
    "TMUS": ("티모바일",),
    "INTC": ("인텔",),
    "QCOM": ("퀄컴",),
    "AMAT": ("어플라이드머티리얼즈",),
    "BKNG": ("부킹홀딩스",),
    # 일본
    "7203": ("도요타", "토요타"),
    "6758": ("소니",),
    "9984": ("소프트뱅크",),
    "6861": ("키엔스",),
    "7974": ("닌텐도",),
    "6098": ("리크루트",),
    "4063": ("신에츠",),
    "6501": ("히타치",),
}


def _build() -> tuple[list[dict], dict, dict, dict]:
    raw = _load_raw()
    minted = _minted()

    markets: list[dict] = []
    quote_specs: dict[str, dict] = {}
    names: dict[str, str] = {}
    aliases: dict[str, tuple[str, ...]] = {}
    flags: dict[str, str] = {}          # 종목 → 그 종목이 속한 시장의 국기

    for key, block in raw.items():
        meta = _MARKET_META.get(key, {"country": "", "flag": "", "desc": ""})
        rows = block.get("ok", [])
        tradable_rows = [r for r in rows if f"{r['ticker']}x" in minted]

        for r in rows:
            t = r["ticker"]
            quote_specs[t] = r["spec"]
            names[t] = r.get("name") or t
            flags[t] = meta.get("flag", "")

        markets.append({
            "key": key,
            "name": block.get("name", key),
            "country": meta["country"],
            "flag": meta.get("flag", ""),
            "desc": meta["desc"],
            # 살 수 있는 것만 목록에 넣는다. 시세만 있고 민트가 없으면
            # 룰북에 넣어봐야 체결에서 죽는다.
            "tickers": [r["ticker"] for r in tradable_rows],
            "tradable": bool(tradable_rows),
            "note": "" if tradable_rows else
                    (f"시세는 확인됐지만({len(rows)}종) devnet 미러 토큰이 "
                     f"아직 없습니다 — mint_markets.py 로 발행하세요."
                     if rows else "종목이 없습니다."),
        })

    # 종목명을 그대로 별칭으로 쓴다. "삼성전자 어때?" 를 알아들으려면
    # 이 표가 있어야 하고, 이름은 KIS 응답에서 온 것이라 지어낸 게 아니다.
    for t, n in names.items():
        cand = {t.lower(), n.lower()}
        cand.discard("")
        # 해외 종목은 KIS 가 이름을 티커로만 준다(NVDA → "NVDA"). 그래서
        # "엔비디아 사줘" 가 종목 없는 주문이 됐다 — 한국어로 쓰는 화면인데
        # 한국어 종목명을 못 알아듣는 셈이었다. 아래 표로 메운다.
        cand |= {a.lower() for a in _KO_ALIASES.get(t, ())}
        aliases[t] = tuple(sorted(cand))

    return markets, quote_specs, names, aliases, flags


MARKETS, QUOTE_SPEC, NAMES, ALIASES, FLAGS = _build()
BY_KEY = {m["key"]: m for m in MARKETS}
DEFAULT_KEYS = ["us-nasdaq"] if "us-nasdaq" in BY_KEY else (
    [MARKETS[0]["key"]] if MARKETS else [])


def reload() -> None:
    """민트를 새로 발행한 뒤 다시 읽는다 (서버 재시작 없이)."""
    global MARKETS, QUOTE_SPEC, NAMES, ALIASES, BY_KEY
    MARKETS, QUOTE_SPEC, NAMES, ALIASES, FLAGS = _build()
    BY_KEY = {m["key"]: m for m in MARKETS}


def base(ticker: str) -> str:
    """미러 토큰 표기(NVDAx) → 실제 티커(NVDA)."""
    return ticker[:-1] if ticker.endswith("x") else ticker


def quote_spec(ticker: str) -> dict | None:
    """이 종목의 KIS 조회 방법. 국내는 code, 해외는 excd."""
    return QUOTE_SPEC.get(base(ticker).upper()) or QUOTE_SPEC.get(base(ticker))


def company(ticker: str) -> str:
    """사람이 읽는 종목명. 모르면 티커 그대로."""
    b = base(ticker)
    return NAMES.get(b) or NAMES.get(b.upper()) or b


def flag(ticker: str) -> str:
    """이 종목이 어느 나라 것인지 한 글자로. 모르면 빈 문자열."""
    b = base(ticker)
    return FLAGS.get(b) or FLAGS.get(b.upper()) or ""


def find_tickers(text: str, allowed: list[str] | None = None) -> list[str]:
    """문장에서 종목을 알아낸다. 못 찾으면 빈 목록.

    allowed 를 주면 그 안에서만 찾는다 — 봇이 거래하지도 않는 종목의
    뉴스를 굳이 받아올 이유가 없고, 뉴스 API 한도도 아껴야 한다.

    ⚠️ 짧은 별칭이 오탐을 낸다. 일본 종목코드 '7203' 같은 숫자나 두 글자
    이름은 문장 어디에나 우연히 들어갈 수 있어서, 3글자 미만은 건너뛴다.
    """
    low = text.lower()
    pool = allowed if allowed is not None else list(ALIASES)
    out = []
    for t in pool:
        b = base(t)
        for name in ALIASES.get(b, (b.lower(),)):
            if _alias_hit(name, low):
                if b not in out:
                    out.append(b)
                break
    return out


_HANGUL_ONLY = re.compile(r"^[가-힣]+$")

# 종목명 뒤에 붙는 한국어 조사. 긴 것부터 적는다 — 정규식 대안은 먼저
# 맞는 것을 택하므로 "이"가 "이랑"보다 앞에 오면 "구글이랑"에서 "이"만
# 먹고 뒤에 "랑"이 남아 경계 검사에 걸린다.
_PARTICLES = ("이랑", "에서", "으로", "부터", "까지", "보다", "처럼", "한테",
              "에게", "은", "는", "이", "가", "을", "를", "와", "과", "랑",
              "도", "만", "에", "의", "로")
_PARTICLE_RE = "|".join(_PARTICLES)


def _alias_hit(name: str, low: str) -> bool:
    """별칭이 문장에 나왔는가.

    3글자 이상은 그냥 부분일치로 본다(기존 규칙).

    2글자는 **한글일 때만** 허용하고, 더 긴 낱말의 일부이면 세지 않는다.
    애플·구글·메타·소니·인텔 처럼 실제로 2글자인 종목명이 여럿인데,
    3글자 규칙을 그대로 두면 "애플 사줘" 가 종목 없는 주문이 된다.
    반대로 경계를 안 보면 "메타버스" 가 META 로 잡힌다.

    경계는 '조사까지만 허용' 으로 잡는다. 한국어는 조사가 명사에 붙어
    쓰이므로("구글이랑") 뒤에 한글이 오면 무조건 배제할 수가 없다. 대신
    붙을 수 있는 것을 조사로 한정하면 둘이 갈린다:
        구글 + 이랑  → 조사      → 종목
        메타 + 버스  → 조사 아님 → 다른 낱말
        애플 + 리케이션 → 조사 아님 → 다른 낱말

    2글자 라틴 별칭은 계속 막는다. 일본 종목코드('7203')나 짧은 티커는
    문장 어디에나 우연히 들어갈 수 있고, 그게 원래 이 규칙의 이유였다.
    """
    if len(name) >= 3:
        return name in low
    if len(name) < 2 or not _HANGUL_ONLY.match(name):
        return False
    pat = rf"{re.escape(name)}(?:{_PARTICLE_RE})?(?![가-힣])"
    return re.search(pat, low) is not None


def tickers_for(keys: list[str]) -> list[str]:
    """고른 시장들에 실제로 상장된(=미러 토큰이 있는) 종목 전부."""
    out: list[str] = []
    for k in keys:
        for t in BY_KEY.get(k, {}).get("tickers", []):
            if t not in out:
                out.append(t)
    return out


def names_for(keys: list[str]) -> list[str]:
    """화면·프롬프트에 쓸 시장 이름. 모르는 키는 그대로 흘린다."""
    return [BY_KEY[k]["name"] if k in BY_KEY else k for k in keys]


def countries_for(keys: list[str]) -> list[str]:
    return sorted({BY_KEY[k]["country"] for k in keys if k in BY_KEY})


def untradable(keys: list[str]) -> list[str]:
    """고른 것 중 거래할 수 없는 시장. 생성·수정에서 거절 사유가 된다."""
    return [k for k in keys if not BY_KEY.get(k, {}).get("tradable")]


# 시장 개념이 생기기 전(2026-08-02 이전)에 만들어진 프로필은 나라 이름을
# 들고 있다. 그대로 두면 설정 화면에서 아무 시장도 선택되지 않은 것처럼
# 보이고, 사용자는 자기가 뭘 골랐었는지 알 수 없게 된다.
_LEGACY = {"미국": "us-nasdaq", "대한민국": "kospi", "일본": "jp-tse"}


def normalize(keys: list[str]) -> list[str]:
    """옛 프로필의 나라 이름을 시장 키로 옮긴다. 모르는 값은 버린다."""
    out: list[str] = []
    for k in keys:
        k = k if k in BY_KEY else _LEGACY.get(k, "")
        if k and k not in out:
            out.append(k)
    return out or list(DEFAULT_KEYS)
