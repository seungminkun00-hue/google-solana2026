"""세션 — 이 브라우저가 만든 것만 이 브라우저에 보이게 한다.

[왜 필요한가]
서버는 하나이고 `BOTS` 도 하나다. 심사용 링크를 여러 명에게 뿌리면
A 가 만든 봇이 B 화면에 그대로 뜨고, B 가 그걸 지울 수도 있었다.
심사위원 지갑도 슬롯이 하나라 나중에 연결한 사람이 앞사람을 덮어썼다.

그래서 브라우저마다 세션 ID 를 하나 발급하고, **봇과 심사위원 지갑을
그 ID 에 묶는다.** 로그인이 아니다 — 신원을 확인하는 것이 아니라
'같은 브라우저인가'만 가른다. 심사 링크에 회원가입을 붙일 수는 없다.

[무엇을 지키고 무엇을 안 지키나]
지킨다   남의 봇을 보거나 고치거나 지우지 못한다. 세션이 다르면 404 다.
안 지킨다 세션 ID 를 훔쳐 쓰면 그 사람 행세를 할 수 있다. 브라우저에
         저장되는 값이라 그렇다. 실서비스라면 서버 세션과 로그인이
         필요하고, 이건 심사 링크용 격리다.

[세션이 없는 요청]
검증 스크립트(verify_scenario.py·audit.py)와 `/demo/seed` 는 헤더를 안 붙인다.
그런 봇은 `session=""` 이 되고 **모두에게 보인다** — 예전과 똑같이 동작해야
23·25항목 검증이 계속 통과하기 때문이다. 운영 중에는 그런 봇이 생기지 않는다.
"""
from __future__ import annotations

import re

from fastapi import Header

# 브라우저가 만들어 보내는 값. 길이와 문자를 제한해 로그·파일명에 그대로
# 실려도 안전하게 한다(지갑 이름의 일부가 되므로 특히 중요하다).
_SAFE = re.compile(r"[^A-Za-z0-9_-]")
MAX_LEN = 32

# 세션 없이 만들어진 것들이 갖는 값. 모두에게 보인다.
PUBLIC = ""


def clean(raw: str | None) -> str:
    if not raw:
        return PUBLIC
    return _SAFE.sub("", raw)[:MAX_LEN]


def session_id(x_session: str | None = Header(default=None)) -> str:
    """요청을 보낸 브라우저. 헤더가 없으면 빈 문자열(공용)."""
    return clean(x_session)


def owns(bot, session: str) -> bool:
    """이 세션이 이 봇을 볼 수 있는가.

    자기 것이거나, 세션 없이 만들어진 공용 봇이면 보인다.
    """
    return getattr(bot, "session", PUBLIC) in (session, PUBLIC)
