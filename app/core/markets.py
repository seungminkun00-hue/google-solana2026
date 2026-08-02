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
            if len(name) >= 3 and name in low:
                if b not in out:
                    out.append(b)
                break
    return out


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
